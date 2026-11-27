#!/usr/bin/env node
/** Discord voice capture, Groq transcription, and Deepgram playback. */

const { Client, Events, GatewayIntentBits, Options } = require('discord.js');
const { joinVoiceChannel, createAudioPlayer, createAudioResource, AudioPlayerStatus, EndBehaviorType, VoiceConnectionStatus, entersState } = require('@discordjs/voice');
const prism = require('prism-media');
const { createTranscriptionQueue } = require('./transcription_queue');
const { configuredKeys } = require('./provider_keys');
const axios = require('axios');
const FormData = require('form-data');
const { Readable } = require('node:stream');
const path = require('path');
require('./runtime_logging').startVoiceLog(path.resolve(__dirname, '..', 'logs'));
require('dotenv').config({ path: path.resolve(__dirname, '..', '.env') });
// Backward compatibility for existing local installs: use the old voice-only
// file only when the shared root environment does not provide Deepgram.
if (!configuredKeys('DEEPGRAM_API_KEY').length) {
    require('dotenv').config({ path: path.resolve(__dirname, '.env') });
}

/**
 * Get formatted timestamp for logging
 */
function getTimestamp() {
    const now = new Date();
    return now.toISOString().replace('T', ' ').substring(0, 19);
}

/**
 * Simple timing tracker
 */
class TimingTracker {
    constructor(operationName) {
        this.operationName = operationName;
        this.startTime = Date.now();
    }

    checkpoint(name) {
        const elapsed = Date.now() - this.startTime;
        console.log(`[${getTimestamp()}] [TIMING] ${this.operationName} - ${name}: ${elapsed}ms`);
    }

    end() {
        const total = Date.now() - this.startTime;
        console.log(`[${getTimestamp()}] [TIMING] ${this.operationName} - TOTAL: ${total}ms`);
        return total;
    }
}

// Configuration
const DISCORD_TOKEN = process.env.DISCORD_TOKEN;
const PYTHON_API_URL = process.env.PYTHON_API_URL || 'http://127.0.0.1:8000';

async function reportServiceUsage(service, durationMs, guildId, userId) {
    try {
        await axios.post(`${PYTHON_API_URL}/usage/service`, {
            service,
            duration_ms: durationMs,
            guild_id: guildId || null,
            user_id: userId || null
        }, { timeout: 2000 });
    } catch (error) {
        console.warn(`[Usage] Could not record ${service} time: ${error.message}`);
    }
}
const GROQ_API_KEYS = configuredKeys('GROQ_API_KEY');
const DEEPGRAM_API_KEYS = configuredKeys('DEEPGRAM_API_KEY');

// Groq API endpoints (STT only)
const GROQ_STT_MODEL = process.env.GROQ_STT_MODEL || 'whisper-large-v3';
const GROQ_STT_LANGUAGE = process.env.GROQ_STT_LANGUAGE || 'en';
const GROQ_API_URL = 'https://api.groq.com/openai/v1';

// Deepgram TTS API (Flux v2)
const DEEPGRAM_TTS_MODEL = process.env.DEEPGRAM_TTS_MODEL || 'flux-haley-en';
const DEEPGRAM_API_URL = 'https://api.deepgram.com/v2/speak';

// The Python process owns slash-command registration; this client shares its token.
const client = new Client({
    // Only Python reads text messages. This client needs guild and voice state.
    makeCache: Options.cacheWithLimits({ ...Options.DefaultMakeCacheSettings, MessageManager: 0 }),
    intents: [
        GatewayIntentBits.Guilds,
        GatewayIntentBits.GuildVoiceStates,
        GatewayIntentBits.GuildMembers
    ]
});

// Voice connection tracking
const voiceConnections = new Map();
const audioPlayers = new Map();
const audioBuffers = new Map();
const voiceChannels = new Map(); // Store voice channel references
const textChannels = new Map(); // The server channel used for text replies

const deafenedGuilds = new Set();
const speechControllers = new Map();
const voiceTurns = new Map();
const captureVersions = new Map();

async function sendVoiceText(guildId, text) {
    const channel = getTextChannel(guildId);
    if (!channel?.isSendable()) {
        console.warn('[Voice] Text reply channel unavailable for guild', guildId);
        return;
    }
    try {
        for (let offset = 0; offset < text.length; offset += 1900) {
            await channel.send({ content: text.slice(offset, offset + 1900), allowedMentions: { parse: [] } });
        }
    } catch (error) {
        console.warn('[Voice] Could not send text reply:', error.message);
    }
}

function getTextChannel(guildId) {
    const guild = client.guilds.cache.get(guildId);
    if (!guild) return null;
    const configured = textChannels.get(guildId);
    const configuredChannel = configured && guild.channels.cache.get(configured);
    if (configuredChannel?.isTextBased()) return configuredChannel;
    return null;
}

async function notifyPythonVoiceStatus(guildId, inVoice, channelId = null) {
    try {
        await axios.post(`${PYTHON_API_URL}/voice/status`, { guild_id: guildId, in_voice: inVoice, channel_id: channelId }, { timeout: 2000 });
    } catch (error) {
        console.error('[Voice] Could not update observed connection status:', error.message);
    }
}

// Response generation tracking to prevent overlaps
const pendingResponses = new Map(); // guildId -> pending response
const ttsStates = new Map(); // guildId -> playback and generation state
const guildTurns = new Map();
const speechLanguages = new Map();
const voiceIssueTimes = new Map();

function reportProviderIssue(guildId, service, code) {
    if (!guildId) return;
    const key = guildId + ':' + service;
    const now = Date.now();
    if (now - (voiceIssueTimes.get(key) || 0) < 120000) return;
    voiceIssueTimes.set(key, now);
    void axios.post(PYTHON_API_URL + '/voice/providerissue', {
        guild_id: guildId, service, code: String(code).replace(/[^A-Za-z0-9_. -]/g, '').slice(0, 40) || 'request error',
    }, { timeout: 2000 }).catch(() => {});
}

function enqueueGuildTurn(guildId, cancelled, run) {
    const queue = guildTurns.get(guildId) || { tail: Promise.resolve(), pending: 0 };
    if (queue.pending >= 12) {
        console.warn('[Voice] Turn queue full in guild', guildId);
        return Promise.resolve();
    }
    queue.pending++;
    guildTurns.set(guildId, queue);
    const turn = queue.tail.then(() => {
        if (!cancelled()) return run();
        console.warn('[Voice] Queued turn expired or connection changed in guild', guildId);
    });
    queue.tail = turn.catch(error => console.warn('[Voice] Queued turn failed:', error.message)).finally(() => {
        if (--queue.pending === 0 && guildTurns.get(guildId) === queue) guildTurns.delete(guildId);
    });
    return queue.tail;
}

async function speechLanguage(guildId) {
    const connection = voiceConnections.get(guildId);
    const cached = speechLanguages.get(guildId);
    if (cached && cached.expires > Date.now()) return cached.code;
    try {
        const { data } = await axios.get(PYTHON_API_URL + '/voice/language/' + guildId, { timeout: 1500 });
        const code = /^[a-z]{2}$/.test(data.language) ? data.language : GROQ_STT_LANGUAGE;
        if (connection && voiceConnections.get(guildId) === connection) {
            speechLanguages.set(guildId, { code, expires: Date.now() + 10000 });
        }
        return code;
    } catch {
        return cached?.code || GROQ_STT_LANGUAGE;
    }
}

function getTTSState(guildId) {
    if (!ttsStates.has(guildId)) {
        ttsStates.set(guildId, {
            isPlaying: false,
            isGenerating: false,
            generationId: 0
        });
    }
    return ttsStates.get(guildId);
}

// Tracks which userIds currently have a live subscribe->decode pipeline attached,
// so speaking 'start' firing again (which happens more often than the stream's
// own silence timeout closes it) doesn't stack duplicate listeners on the same
// cached Opus stream.
const activePipelines = new Map();

// Debounce for end-of-speech: Discord's speaking indicator can fire 'end'
// then 'start' again within milliseconds mid-sentence (breaths, brief pauses),
// so a flush scheduled on 'end' gets cancelled here if 'start' fires again
// before the debounce window elapses, meaning it wasn't really the end of the
// utterance yet.
const pendingFlushTimeouts = new Map(); // guild:user -> Timeout
const FLUSH_DEBOUNCE_MS = 300;
const SILENCE_END_MS = 300;

function resetCapture(guildId, connection) {
    audioBuffers.delete(guildId);
    captureVersions.set(guildId, Symbol());
    for (const [key, timeout] of pendingFlushTimeouts) {
        if (key.startsWith(guildId + ':')) {
            clearTimeout(timeout);
            pendingFlushTimeouts.delete(key);
        }
    }
    for (const subscription of connection?.receiver.subscriptions.values() || []) subscription.destroy();
}

function isVoiceMemberMuted(member) {
    return !member?.voice || member.voice.mute || member.voice.selfMute || member.voice.deaf || member.voice.selfDeaf;
}

function activeVoiceMember(channel, userId) {
    const state = channel?.guild.voiceStates.cache.get(userId);
    return state && state.channelId === channel.id ? state.member : null;
}

/**
 * Handle incoming audio chunks
 */
function handleAudioChunk(guildId, userId, chunk) {
    if (!audioBuffers.has(guildId)) {
        audioBuffers.set(guildId, new Map());
    }

    const userBuffers = audioBuffers.get(guildId);
    if (!userBuffers.has(userId)) {
        userBuffers.set(userId, []);
    }

    userBuffers.get(userId).push(chunk);
    // Keep complete utterances unless continuous speech reaches the ~15s cap.
    const chunks = userBuffers.get(userId);
    if (chunks.length > 750) {
        console.log(`[Voice] Safety cap reached for user ${userId}, forcing flush (${chunks.length} chunks)`);
        flushUserAudio(guildId, userId);
    }
}

/**
 * Flush whatever audio has accumulated for a user and send it for
 * transcription as one complete utterance. Called when Discord's speaking
 * indicator reports the user stopped talking.
 */
function flushUserAudio(guildId, userId) {
    const userBuffers = audioBuffers.get(guildId);
    if (!userBuffers || !userBuffers.has(userId)) return;

    const chunks = userBuffers.get(userId);
    if (chunks.length === 0) return;

    console.log(`[Voice] Flushing audio from user ${userId} (${chunks.length} chunks)`);
    userBuffers.delete(userId);
    if (!userBuffers.size) audioBuffers.delete(guildId);
    if (deafenedGuilds.has(guildId)) return;
    const member = activeVoiceMember(voiceChannels.get(guildId), userId);
    if (!member || member.user.bot) return;
    const key = guildId + ':' + userId;
    const active = voiceTurns.get(key) || { last: null, pending: 0 };
    if (active.pending >= 4) {
        console.warn("[Voice] Ignored a clip because the speaker has four unfinished turns");
        return;
    }
    const connection = voiceConnections.get(guildId);
    const version = captureVersions.get(guildId);
    const capturedAt = Date.now();
    const cancelled = () => voiceConnections.get(guildId) !== connection || deafenedGuilds.has(guildId)
        || captureVersions.get(guildId) !== version || !activeVoiceMember(voiceChannels.get(guildId), userId)
        || Date.now() - capturedAt > 58000;
    active.pending++;
    active.last = processAudio(guildId, userId, chunks, active.last, cancelled);
    voiceTurns.set(key, active);
    void active.last.finally(() => {
        if (--active.pending === 0 && voiceTurns.get(key) === active) voiceTurns.delete(key);
    });
}

/**
 * Filter out noise transcriptions
 * STT often hallucinates "thank you" from minor sounds, and repeats words
 */
function filterNoiseTranscript(transcript) {
    if (!transcript) return null;

    const text = transcript.trim().toLowerCase();
    const words = text.split(/\s+/);

    console.log(`[Voice] Filtering transcript: "${transcript}" (${words.length} words)`);

    // Common misrecognitions of "Jenny" to treat as name mentions
    const mentionsJenny = /\b(?:jenny(?:\s*belle)?|jenni|jennie|jeni|chani|china|shani|shaani|jennybell)\b/i.test(text);

    // Filter 1: Specifically filter standalone "thank you" and similar (with or without punctuation) unless "jenny" is mentioned
    if ((text === 'thank you' || text === 'thanks' || text === 'thank' || 
        text === 'thank you.' || text === 'thanks.' || text === 'thank.' ||
        text === 'thank you!' || text === 'thanks!' || text === 'thank!') && !mentionsJenny) {
        console.log('[Voice] Filtered noise transcription (standalone thanks):', transcript);
        return null;
    }

    // Filter 2: Ignore transcriptions that are ONLY common STT hallucinations without "jenny" or similar
    const noisePhrases = ['thank you', 'thanks', 'thank', 'you', 'ok', 'okay', 'yeah', 'yep', 'yes', 'no', 'uh', 'um', 'ah', 'oh', 'mm', 'hm', 'hmm'];
    
    if (words.length <= 3 && noisePhrases.includes(text) && !mentionsJenny) {
        console.log('[Voice] Filtered noise transcription (noise phrase without jenny):', transcript);
        return null;
    }

    // Filter 3: Detect repeated words/phrases (e.g., "you you you", "thank you thank you")
    const wordCounts = {};
    words.forEach(word => {
        wordCounts[word] = (wordCounts[word] || 0) + 1;
    });

    // If any word appears more than 2 times in a short transcription, it's noise
    const maxRepeats = Math.max(...Object.values(wordCounts));
    if (maxRepeats > 2 && words.length < 10) {
        console.log('[Voice] Filtered noise transcription (repeated words):', transcript);
        return null;
    }

    // Filter 4: Very short single-word transcriptions without "jenny" or similar
    if (words.length === 1 && !mentionsJenny) {
        console.log('[Voice] Filtered noise transcription (single word without jenny):', transcript);
        return null;
    }

    // Log if Jenny (or similar) was detected
    if (mentionsJenny) {
        console.log('[Voice] Jenny (or similar) detected in transcript:', transcript);
    }

    console.log(`[Voice] Transcript passed noise filter: "${transcript}"`);
    return transcript;
}

/**
 * Process accumulated audio chunks
 */
async function processAudio(guildId, userId, chunks, previous, cancelled) {
    const timing = new TimingTracker('Audio Processing');
    const timingsMs = { speech_capture: Math.max(0, chunks.length * 20) };
    
    try {
        console.log(`[${getTimestamp()}] [Voice] Processing audio - combining ${chunks.length} chunks`);
        // Copy the PCM and WAV header into one buffer, then release the source chunks.
        let stageStartedAt = Date.now();
        let wavBuffer = pcmToWav(chunks);
        chunks.length = 0;
        timingsMs.wav_conversion = Date.now() - stageStartedAt;
        console.log(`[${getTimestamp()}] [Voice] WAV conversion complete, size: ${wavBuffer.length} bytes`);
        timing.checkpoint('WAV conversion');

        // Transcribe using Groq
        console.log(`[${getTimestamp()}] [Voice] Sending to Groq for transcription...`);
        stageStartedAt = Date.now();
        const transcript = await transcribeAudio(wavBuffer, guildId, userId, cancelled);
        wavBuffer = null;
        timingsMs.stt = Date.now() - stageStartedAt;
        console.log(`[${getTimestamp()}] [Voice] Transcription result: "${transcript}"`);
        timing.checkpoint('STT (Groq)');
        
        if (cancelled()) return;
        if (!transcript) {
            console.log(`[${getTimestamp()}] [Voice] No transcript returned, skipping processing`);
            timing.end();
            return;
        }
        
        // Check for cancellation phrases if there's a pending response
        const pendingResponse = pendingResponses.get(guildId);
        if (pendingResponse && pendingResponse.userId === userId) {
            const lowerTranscript = transcript.toLowerCase();
            const cancellationPhrases = [
                'never mind', 'don\'t reply', 'no reply', 'cancel', 'stop', 'nevermind',
                'actually don\'t', 'actually no', 'forget it', 'ignore that'
            ];
            
            // Common misrecognitions of "Jenny" to treat as name mentions
            const jennyVariations = ['jenny', 'chani', 'china', 'shani', 'shaani', 'jennybelle', 'jenni', 'jennie', 'jeni'];
            
            // Check if any cancellation phrase is present AND Jenny's name was recognized
            const hasCancellation = cancellationPhrases.some(phrase => lowerTranscript.includes(phrase));
            const mentionsJenny = jennyVariations.some(variation => new RegExp(`\\b${variation}\\b`, 'i').test(lowerTranscript));
            
            if (hasCancellation && mentionsJenny) {
                console.log('[Voice] Cancellation detected, aborting pending response');
                await stopTTSPlayback(guildId);
                return; // Don't process this transcription either
            }
        }
        
        // Get username from Discord (use display name instead)
        const guild = client.guilds.cache.get(guildId);
        const member = guild?.members.cache.get(userId);
        let displayName = member ? member.displayName : 'Unknown';

        // Clean up display name: remove numbers and truncate if too long
        displayName = displayName.replace(/\d+/g, '').trim(); // Remove numbers
        if (displayName.length > 20) {
            displayName = displayName.split(' ')[0].substring(0, 20); // Take first word, max 20 chars
        }
        console.log(`[${getTimestamp()}] [Voice] Using cleaned display name: "${displayName}" (from: ${member?.displayName})`);

        // Get current voice channel ID
        const voiceChannel = voiceChannels.get(guildId);
        const channelId = voiceChannel ? voiceChannel.id : null;

        // A voice turn always speaks. The AI may attach a separate chat copy
        // when the speaker explicitly asks for one; chat-message preferences
        // from /replymode never alter this voice-input path.
        console.log(`[${getTimestamp()}] [Voice] Voice input will receive a spoken reply`);

        // Send to Python API for processing
        timing.checkpoint('Before Python API');
        await previous;
        await enqueueGuildTurn(guildId, cancelled, () => sendToPythonAPI(
            userId, displayName, transcript, guildId, channelId, timingsMs, timingsMs.speech_capture,
        ));
        timingsMs.total_pipeline = Date.now() - timing.startTime;
        try {
            await axios.post(`${PYTHON_API_URL}/voice/turn/timing`, {
                guild_id: guildId,
                user_id: userId,
                username: displayName,
                transcript,
                timings_ms: { total_pipeline: timingsMs.total_pipeline }
            }, { timeout: 2000 });
        } catch (timingError) {
            console.warn(`[Voice] Could not save total pipeline time: ${timingError.message}`);
        }
        timing.end();
    } catch (error) {
        console.error(`[${getTimestamp()}] [Voice] Error processing audio:`, error);
        timing.end();
    }
}

/**
 * Transcribe audio using Groq Whisper
 */
const sttQueue = createTranscriptionQueue(GROQ_API_KEYS, async (key, { audioBuffer, language, guildId }, timeout) => {
    const form = new FormData();
    form.append('file', audioBuffer, { filename: 'audio.wav', contentType: 'audio/wav' });
    form.append('model', GROQ_STT_MODEL);
    form.append('language', language);
    form.append('response_format', 'json');
    try {
        const { data } = await axios.post(GROQ_API_URL + '/audio/transcriptions', form, {
            headers: { Authorization: 'Bearer ' + key, ...form.getHeaders() }, timeout,
        });
        return data.text;
    } catch (error) {
        const issue = error.response?.status || error.code || 'request error';
        console.warn('[Voice] Groq STT request failed:', issue);
        reportProviderIssue(guildId, 'stt', issue);
        throw error;
    }
});

async function transcribeAudio(audioBuffer, guildId, userId, cancelled) {
    if (audioBuffer.length < 46_080) return null; // At least 240 ms of 48 kHz stereo PCM.
    const language = await speechLanguage(guildId);
    const result = await sttQueue({ audioBuffer, language, guildId }, cancelled);
    if (!result) {
        if (!cancelled()) console.warn('[Voice] Dropped an expired or failed speech recognition request');
        return null;
    }
    void reportServiceUsage('stt', result.duration, guildId, userId);
    return filterNoiseTranscript(result.text);
}

/**
 * Leave Latin letters, numbers and basic punctuation for the English voice.
 */
function sanitizeTextForTTS(text) {
    return text.normalize('NFC')
        .replace(/https?:\/\/\S+|<a?:[\w-]+:\d+>|<[@#][!&]?\d+>|:[\w+-]+:/gi, ' ')
        .replace(/[0-9#*]\uFE0F?\u20E3/g, ' ')
        .replace(/[‘’]/g, "'")
        .replace(/[“”]/g, '"')
        .replace(/[^\p{Script=Latin}0-9 .,!?"'():;%-]/gu, ' ')
        .replace(/\s+/g, ' ').trim();
}

/**
 * Generate speech using Deepgram Flux TTS (v2)
 */
async function generateSpeech(text, guildId = null, userId = null) {
    const sanitizedText = sanitizeTextForTTS(text);
    if (!/[0-9\p{Script=Latin}]/u.test(sanitizedText) || !DEEPGRAM_API_KEYS.length) return null;

    const controller = new AbortController();
    if (!speechControllers.has(guildId)) speechControllers.set(guildId, new Set());
    const controllers = speechControllers.get(guildId);
    controllers.add(controller);
    const timing = new TimingTracker('TTS Generation');
    const deadline = Date.now() + 25000;

    try {
        for (const [index, key] of DEEPGRAM_API_KEYS.entries()) {
            const remaining = deadline - Date.now();
            if (controller.signal.aborted || remaining <= 0) return null;
            try {
                const response = await axios.post(
                    DEEPGRAM_API_URL + '?model=' + encodeURIComponent(DEEPGRAM_TTS_MODEL) + '&speed=1.1&expressivity=0',
                    { text: sanitizedText },
                    {
                        headers: { Authorization: 'Token ' + key, 'Content-Type': 'application/json' },
                        responseType: 'arraybuffer', signal: controller.signal, timeout: Math.min(remaining, 10000),
                        maxContentLength: 12 * 1024 * 1024,
                    },
                );
                if (controller.signal.aborted) return null;
                const audio = Buffer.isBuffer(response.data) ? response.data : Buffer.from(response.data);
                const contentType = response.headers?.['content-type'] || '';
                if (!audio.length || contentType.includes('json')) {
                    console.warn('[Voice] Deepgram key ' + (index + 1) + ' returned no audio');
                    continue;
                }
                timing.checkpoint('Deepgram response');
                void reportServiceUsage('tts', Date.now() - timing.startTime, guildId, userId);
                return audio;
            } catch (error) {
                if (controller.signal.aborted) return null;
                const detail = error.response?.status || error.code || 'request error';
                console.warn('[Voice] Deepgram key ' + (index + 1) + ' failed:', detail);
                reportProviderIssue(guildId, 'tts', detail);
            }
        }
        return null;
    } finally {
        timing.end();
        controllers.delete(controller);
        if (!controllers.size && speechControllers.get(guildId) === controllers) speechControllers.delete(guildId);
    }
}

/**
 * Check if bot is in a voice channel for a guild
 */
function isInVoiceChannel(guildId) {
    return voiceConnections.has(guildId);
}

/** Stream a chunk from memory and release listeners on every outcome. */
function playAudio(guildId, audioBuffer) {
    const player = audioPlayers.get(guildId);
    if (!player || !isInVoiceChannel(guildId)) return Promise.resolve(false);
    const state = getTTSState(guildId);
    return new Promise(resolve => {
        let resource;
        const idle = () => finish(true);
        const failed = error => {
            console.warn('[Voice] Audio playback failed:', error.message);
            finish(false);
        };
        const timer = setTimeout(() => {
            console.warn('[Voice] Audio playback timed out in guild', guildId);
            finish(false);
            if (resource && player.state.resource === resource) player.stop(true);
        }, 120000);
        const finish = success => {
            clearTimeout(timer);
            player.off(AudioPlayerStatus.Idle, idle);
            player.off('error', failed);
            state.isPlaying = false;
            resolve(success);
        };

        player.once(AudioPlayerStatus.Idle, idle);
        player.once('error', failed);
        try {
            resource = createAudioResource(Readable.from([audioBuffer], { objectMode: false }));
            state.isPlaying = true;
            player.play(resource);
        } catch (error) {
            failed(error);
            resource?.playStream.destroy();
        }
    });
}




/**
 * Cancel all pending TTS operations (race-safe)
 */
function cancelPendingTTS(guildId) {
    const state = ttsStates.get(guildId);
    console.log(`[Voice] Cancelling pending TTS operations for guild ${guildId}`);
    if (state) {
        state.generationId += 1;
        state.isGenerating = false;
        state.isPlaying = false;
    }
    pendingResponses.delete(guildId);
    const player = audioPlayers.get(guildId);
    if (player) {
        try { player.stop(true); } catch (error) {
            console.error(`[Voice] Error stopping player for guild ${guildId}:`, error);
        }
    }
}

function releaseVoiceConnection(guildId, connection) {
    if (voiceConnections.get(guildId) !== connection) return;
    // Remove the identity before destroying the connection, which emits events.
    voiceConnections.delete(guildId);
    stopTTSPlayback(guildId);
    resetCapture(guildId, connection);
    captureVersions.delete(guildId);
    deafenedGuilds.delete(guildId);
    if (connection.state.status !== VoiceConnectionStatus.Destroyed) connection.destroy();
    voiceChannels.delete(guildId);
    audioPlayers.delete(guildId);
    ttsStates.delete(guildId);
    speechLanguages.delete(guildId);
    voiceIssueTimes.delete(guildId + ':stt');
    voiceIssueTimes.delete(guildId + ':tts');
}

/**
 * Stop TTS playback (interruption)
 */
function stopTTSPlayback(guildId) {
    for (const controller of speechControllers.get(guildId) || []) controller.abort();
    try {
        cancelPendingTTS(guildId);
        return true;
    } catch (error) {
        console.error('[Voice] Error stopping TTS playback:', error);
        return false;
    }
}

/**
 * Split text into sentence chunks for TTS
 * Groups 2-3 sentences per chunk for better flow
 * Keep common abbreviations together when splitting sentences.
 */
function splitIntoSentences(text) {
    // Handle empty or very short text
    if (!text || text.trim().length < 5) {
        return [text];
    }

    // Common abbreviations that shouldn't be split after
    const abbreviations = ['Mr.', 'Mrs.', 'Ms.', 'Dr.', 'Prof.', 'Sr.', 'Jr.', 'St.', 'vs.', 'etc.', 'e.g.', 'i.e.', 'approx.', 'ca.', 'est.', 'sq.', 'No.', 'Ave.', 'Blvd.', 'Rd.', 'Hwy.', 'mt.', 'ft.', 'in.', 'cm.', 'mm.', 'km.', 'kg.', 'lb.', 'oz.', 'vol.', 'ch.', 'fig.', 'pt.', 'p.', 'a.m.', 'p.m.', 'A.M.', 'P.M.'];
    
    // Detect if this might be a song/poem (contains musical notes, multiple line breaks, or artistic formatting)
    const isArtisticContent = /[\*♫♪]/.test(text) || (text.match(/\n/g) || []).length >= 2;
    
    // Simple sentence splitting - split on . ! ? but avoid abbreviations
    const sentences = [];
    let currentSentence = '';
    
    for (let i = 0; i < text.length; i++) {
        const char = text[i];
        currentSentence += char;
        
        // Check for sentence-ending punctuation (only for artistic content, use ! ? more aggressively)
        if (char === '!' || char === '?' || char === '。' || char === '！' || char === '？') {
            sentences.push(currentSentence.trim());
            currentSentence = '';
        } else if (char === '.' && !isArtisticContent) {
            // Only split on periods for non-artistic content
            // Check if this is an abbreviation
            const lastFewChars = currentSentence.slice(-10).toLowerCase();
            const isAbbrev = abbreviations.some(abbr => lastFewChars.includes(abbr.toLowerCase()));
            
            // Check if it's a decimal number
            const isDecimal = /\d+\.\d*$/.test(currentSentence.trim());
            
            if (!isAbbrev && !isDecimal) {
                sentences.push(currentSentence.trim());
                currentSentence = '';
            }
        }
    }
    
    // Add any remaining text
    if (currentSentence.trim()) {
        sentences.push(currentSentence.trim());
    }
    
    // Fallback: if no sentences were found, just return the original text
    if (sentences.length === 0) {
        return [text];
    }
    
    // Group sentences into chunks
    const chunks = [];
    let currentChunk = '';
    let sentencesInChunk = 0;
    
    for (const sentence of sentences) {
        const sentenceLength = sentence.length;
        
        // For artistic content, use larger chunks to preserve flow
        const maxChunkSize = isArtisticContent ? 300 : 200;
        const longSentenceThreshold = isArtisticContent ? 150 : 100;
        
        // If sentence is very long, make it its own chunk
        if (sentenceLength > longSentenceThreshold) {
            if (currentChunk) {
                chunks.push(currentChunk.trim());
                currentChunk = '';
                sentencesInChunk = 0;
            }
            chunks.push(sentence);
            continue;
        }
        
        // If adding this sentence would make chunk too long, start new chunk
        if (currentChunk.length + sentenceLength > maxChunkSize) {
            if (currentChunk) {
                chunks.push(currentChunk.trim());
                currentChunk = '';
                sentencesInChunk = 0;
            }
        }
        
        // Add sentence to current chunk
        currentChunk += (currentChunk ? ' ' : '') + sentence;
        sentencesInChunk++;
        
        // For artistic content, group more sentences together
        const minSentencesForChunk = isArtisticContent ? 3 : 2;
        const maxSentencesForChunk = isArtisticContent ? 5 : 3;
        
        // If we have enough sentences in chunk, start a new one
        if (sentencesInChunk >= minSentencesForChunk && (sentenceLength > 30 || sentencesInChunk >= maxSentencesForChunk)) {
            chunks.push(currentChunk.trim());
            currentChunk = '';
            sentencesInChunk = 0;
        }
    }
    
    // Add any remaining chunk
    if (currentChunk.trim()) {
        chunks.push(currentChunk.trim());
    }
    
    // Filter out empty chunks
    return chunks.filter(s => s.length > 0);
}

/**
 * Clean text for TTS by removing non-Latin characters (Arabic, etc.)
 * Deepgram TTS may break with non-Latin characters
 */
/**
 * Generate sentence chunks in parallel, then play each as soon as its audio is ready.
 * Later sentences can synthesize while the first sentence is already playing.
 */
async function playTTSInChunks(guildId, text, userId = null) {
    if (!text || /\bCMD\s*:|^\s*(?:NO_RESPONSE|NEED_SEARCH|KILLTTS|JOIN_VC|LEAVE_VC|REPLY_MODE_\w+)\b/i.test(text)) {
        console.warn('[Voice] Ignored empty or control text in speech request');
        return false;
    }
    const cleaned = sanitizeTextForTTS(text);
    if (!cleaned || !isInVoiceChannel(guildId)) return false;
    const chunks = splitIntoSentences(cleaned);
    const state = getTTSState(guildId);
    const generationId = state.generationId;
    const timing = new TimingTracker('TTS Playback');
    state.isGenerating = true;

    const isCurrent = () => state.generationId === generationId && isInVoiceChannel(guildId);
    const synthesize = index => generateSpeech(chunks[index], guildId, userId).catch(error => {
        console.warn('[Voice] Could not synthesize a speech chunk:', error.message);
        return null;
    });
    try {
        let next = synthesize(0);
        let played = 0;
        for (let index = 0; index < chunks.length; index++) {
            const audio = await next;
            if (!isCurrent()) return false;
            const playback = audio && playAudio(guildId, audio);
            // At most one chunk is prepared while the previous chunk plays.
            next = index + 1 < chunks.length ? synthesize(index + 1) : null;
            if (playback) {
                if (!await playback) {
                    await stopTTSPlayback(guildId);
                    return false;
                }
                played++;
            }
        }
        return played > 0 && isCurrent();
    } finally {
        if (state.generationId === generationId) state.isGenerating = false;
        timing.end();
    }
}


/**
 * Send text to Python API for processing
 */
async function sendToPythonAPI(userId, username, transcript, guildId, channelId, timingsMs, speechDurationMs) {
    const timing = new TimingTracker('Python API Request');
    const originalConnection = voiceConnections.get(guildId);
    try {
        const startedAt = Date.now();
        const { data } = await axios.post(PYTHON_API_URL + '/voice/process', {
            user_id: userId, username, transcript, guild_id: guildId, channel_id: channelId,
            reply_mode: 'vc', is_voice_message: true,
            timings_ms: timingsMs, speech_duration_ms: speechDurationMs,
        }, { timeout: 55000 });
        timingsMs.python_api = Date.now() - startedAt;
        if (data.status !== 'success' || voiceConnections.get(guildId) !== originalConnection ||
            deafenedGuilds.has(guildId)) return;

        if (data.chat_message) await sendVoiceText(guildId, data.chat_message);
        const speech = data.response?.trim();
        if (speech?.toUpperCase() === 'KILLTTS') {
            await stopTTSPlayback(guildId);
            return;
        }
        if (!speech || data.is_command || data.reply_mode === 'command') return;
        if (!isInVoiceChannel(guildId)) return;

        const item = { userId };
        pendingResponses.set(guildId, item);
        const ttsStartedAt = Date.now();
        try {
            await playTTSInChunks(guildId, speech, userId);
        } finally {
            if (pendingResponses.get(guildId) === item) pendingResponses.delete(guildId);
        }
        void axios.post(PYTHON_API_URL + '/voice/turn/timing', {
            guild_id: guildId, user_id: userId, username, transcript,
            timings_ms: {
                python_api: timingsMs.python_api,
                tts_generation_and_playback: Date.now() - ttsStartedAt,
            },
        }, { timeout: 2000 }).catch(error => console.warn('[Voice] Could not record speech timing:', error.message));
    } catch (error) {
        console.error('[Voice] Could not complete voice turn:', error.message);
    } finally {
        timing.end();
    }
}

// Discord Opus is decoded to stereo 48 kHz PCM before wrapping it for Whisper.
function pcmToWav(chunks, sampleRate = 48000, channels = 2, bitDepth = 16) {
    const byteRate = sampleRate * channels * (bitDepth / 8);
    const blockAlign = channels * (bitDepth / 8);
    const dataSize = chunks.reduce((total, chunk) => total + chunk.length, 0);
    const header = Buffer.alloc(44);

    header.write('RIFF', 0);
    header.writeUInt32LE(36 + dataSize, 4);
    header.write('WAVE', 8);
    header.write('fmt ', 12);
    header.writeUInt32LE(16, 16);
    header.writeUInt16LE(1, 20); // PCM format
    header.writeUInt16LE(channels, 22);
    header.writeUInt32LE(sampleRate, 24);
    header.writeUInt32LE(byteRate, 28);
    header.writeUInt16LE(blockAlign, 32);
    header.writeUInt16LE(bitDepth, 34);
    header.write('data', 36);
    header.writeUInt32LE(dataSize, 40);

    return Buffer.concat([header, ...chunks], 44 + dataSize);
}

const voiceSessionId = require('node:crypto').randomUUID();
client.once(Events.ClientReady, () => {
    console.log(`[Voice] Voice service logged in as ${client.user.tag}`);
    console.log(`[Voice] Connected to Python API at: ${PYTHON_API_URL}`);
    console.log(`[Voice] Groq STT initialized`);
    console.log(`[Voice] Deepgram TTS initialized`);
    console.log(`[Voice] STT Model: ${GROQ_STT_MODEL}`);
    console.log(`[Voice] TTS Model: ${DEEPGRAM_TTS_MODEL}`);
    console.log('[Voice] Discord.js Voice Receive enabled');
    console.log('[Voice] Ready for real voice input/output');

    const heartbeat = () => {
        const connections = Object.fromEntries([...voiceConnections]
            .filter(([, connection]) => connection.state.status === VoiceConnectionStatus.Ready && connection.joinConfig.channelId)
            .map(([guildId, connection]) => [guildId, connection.joinConfig.channelId]));
        return axios.post(`${PYTHON_API_URL}/voice/service/heartbeat`, {
            session_id: voiceSessionId, connections,
        }, { timeout: 2000 }).catch(error => console.warn(`[Voice] Python API heartbeat failed: ${error.message}`));
    };
    void heartbeat();
    setInterval(heartbeat, 5000);


    // Start polling for voice commands from Python API
    setInterval(pollVoiceCommands, 1000); // Poll every second
});

let fetchingVoiceCommands = false;
const runningVoiceCommands = new Set();

// Ask Python which guilds actually have work instead of polling every server.
async function pollVoiceCommands() {
    if (fetchingVoiceCommands) return;
    fetchingVoiceCommands = true;
    try {
        const { data } = await axios.get(PYTHON_API_URL + '/voice/pending', {
            params: { session_id: voiceSessionId }, timeout: 2000,
        });
        for (const guildId of data.guild_ids) {
            const guild = client.guilds.cache.get(guildId);
            if (guild && !runningVoiceCommands.has(guildId)) void runVoiceCommand(guild);
        }
    } catch (error) {
        if (error.response?.status === 404) {
            // Keep compatibility with the previous Python bridge.
            for (const guild of client.guilds.cache.values()) {
                if (!runningVoiceCommands.has(guild.id)) void runVoiceCommand(guild);
            }
        }
    } finally {
        fetchingVoiceCommands = false;
    }
}

async function runVoiceCommand(guild) {
    const guildId = guild.id;
    runningVoiceCommands.add(guildId);
    try {
        const { data: payload } = await axios.get(PYTHON_API_URL + '/voice/command/' + guildId, {
            params: { session_id: voiceSessionId }, timeout: 2000,
        });
        if (payload.text_channel_id) textChannels.set(guildId, payload.text_channel_id);
        if (payload.status !== 'success' || !payload.command) return;
        const command = payload.command;
        if (command === 'join') {
            const joinId = payload.join_id;
            const report = async (connected, detail = '') => {
                if (!joinId) return true;
                const result = await axios.post(`${PYTHON_API_URL}/voice/join/result`, {
                    guild_id: guildId, join_id: joinId, connected, detail,
                }, { timeout: 1500 });
                return result.data.accepted;
            };
            const existing = voiceConnections.get(guildId);
            if (existing?.state.status === VoiceConnectionStatus.Ready &&
                existing.joinConfig.channelId === payload.channel_id) {
                await report(true);
                return;
            }
            const target = guild.channels.cache.get(payload.channel_id);
            if (!target?.isVoiceBased()) {
                console.warn('[Voice] Dropped join for an unavailable voice channel');
                await report(false, 'That voice channel is unavailable.');
                return;
            }
            console.log(`[Voice] Processing join command for guild ${guild.id}`);

            const voiceChannel = target;
            if (voiceChannel) {
                const currentConnection = voiceConnections.get(guild.id);
                if (currentConnection) await releaseVoiceConnection(guildId, currentConnection);
                console.log(`[Voice] Attempting to join voice channel: ${voiceChannel.name} (${voiceChannel.id})`);

                let connection;
                try {
                    // Direct join without message/interaction wrapper
                    const timeout = Math.min(8500, (payload.remaining_ms ?? 12000) - 1000);
                    if (timeout <= 0) throw new Error('Join request expired before connecting');
                    connection = joinVoiceChannel({
                        channelId: voiceChannel.id,
                        guildId: guild.id,
                        adapterCreator: guild.voiceAdapterCreator,
                        selfDeaf: false,
                        selfMute: false
                    });

                    console.log(`[Voice] Connection created for guild ${guild.id}, channel ${voiceChannel.id}`);

                    // Set up error handler for connection
                    connection.on('error', (error) => {
                        console.error('[Voice] Voice connection error:', error);
                    });

                    await entersState(connection, VoiceConnectionStatus.Ready, timeout);

                    voiceConnections.set(guild.id, connection);
                    voiceChannels.set(guild.id, voiceChannel);

                    const player = createAudioPlayer();
                    audioPlayers.set(guild.id, player);
                    connection.subscribe(player);

                    const receiver = connection.receiver;

                    console.log('[Voice] VoiceReceiver created');
                    console.log(`[Voice] Receiver ready for channel ${voiceChannel.id}`);

                    // Set up speaking event handlers
                    let speakingEventCount = 0;
                    receiver.speaking.on('start', (userId) => {
                        const key = guildId + ':' + userId;
                        speakingEventCount++;
                        if (speakingEventCount % 25 === 0 || speakingEventCount === 1) {
                            console.log(`[Voice] Speaking event fired! Count: ${speakingEventCount}, User: ${userId}`);
                        }

                        if (userId === client.user.id) return;

                        const member = activeVoiceMember(voiceChannel, userId);
                        if (member?.user.bot || deafenedGuilds.has(guildId)) return;
                        if (!member) {
                            console.error('[Voice] Member not found for speaking event');
                            return;
                        }

                        const displayName = member?.displayName || member?.user?.username || userId;
                        if (isVoiceMemberMuted(member)) {
                            const buffer = audioBuffers.get(guild.id);
                            if (buffer) buffer.delete(userId);
                            return;
                        }
                        if (speakingEventCount % 25 === 0 || speakingEventCount === 1) {
                            console.log(`[Voice] ${displayName} started speaking`);
                        }

                        // Cancel any pending flush if user starts speaking again
                        if (pendingFlushTimeouts.has(key)) {
                            clearTimeout(pendingFlushTimeouts.get(key));
                            pendingFlushTimeouts.delete(key);
                            // Only log this occasionally to reduce spam
                            if (speakingEventCount % 25 === 0) {
                                console.log(`[Voice] Cancelled pending flush for ${displayName}, still talking`);
                            }
                        }

                        // Check if user is muted - skip processing if they are
                        if (activePipelines.has(key)) {
                            // Only log this occasionally to reduce spam
                            if (speakingEventCount % 50 === 0) {
                                console.log(`[Voice] Pipeline already active for ${displayName}, skipping`);
                            }
                            return;
                        }

                        let opusStream;
                        try {
                            opusStream = receiver.subscribe(userId, {
                                end: { behavior: EndBehaviorType.AfterSilence, duration: SILENCE_END_MS }
                            });
                            if (!opusStream) {
                                console.error(`[Voice] Failed to subscribe to ${displayName}`);
                                return;
                            }

                            activePipelines.set(key, opusStream);
                            let pcmStream;
                            opusStream.once('close', () => {
                                pcmStream?.destroy();
                                if (activePipelines.get(key) === opusStream) activePipelines.delete(key);
                            });

                            // Use prism-media's opus decoder instead of @discordjs/opus
                            pcmStream = opusStream.pipe(new prism.opus.Decoder({ channels: 2, rate: 48000 }));
                            pcmStream.on('data', (chunk) => {
                                if (deafenedGuilds.has(guildId) || voiceConnections.get(guildId) !== connection) return;
                                handleAudioChunk(guild.id, userId, chunk);
                            });

                            pcmStream.on('error', (error) => {
                                console.error(`[Voice] Opus decode error for ${displayName}:`, error);
                                opusStream.destroy();
                            });
                        } catch (error) {
                            opusStream?.destroy();
                            console.error(`[Voice] Error dynamically subscribing to ${displayName}:`, error);
                        }
                    });

                    receiver.speaking.on('end', (userId) => {
                        const key = guildId + ':' + userId;
                        const member = activeVoiceMember(voiceChannel, userId);
                        if (deafenedGuilds.has(guildId) || member?.user.bot) return;
                        const displayName = member?.displayName || member?.user?.username || userId;

                        if (speakingEventCount % 25 === 0 || speakingEventCount === 1) {
                            console.log(`[Voice] Speaking END: ${displayName} (${userId})`);
                        }

                        // Check if user is muted - skip processing if they are
                        if (isVoiceMemberMuted(member)) {
                            const buffer = audioBuffers.get(guild.id);
                            if (buffer) buffer.delete(userId);
                            return;
                        }

                        if (pendingFlushTimeouts.has(key)) {
                            clearTimeout(pendingFlushTimeouts.get(key));
                            pendingFlushTimeouts.delete(key);
                            // Only log this occasionally to reduce spam
                            if (speakingEventCount % 25 === 0) {
                                console.log(`[Voice] Cancelled pending flush for ${displayName}, still talking`);
                            }
                        }

                        const timeoutHandle = setTimeout(() => {
                            pendingFlushTimeouts.delete(key);
                            flushUserAudio(guild.id, userId);
                        }, FLUSH_DEBOUNCE_MS);
                        pendingFlushTimeouts.set(key, timeoutHandle);
                    });

                    if (!await report(true)) throw new Error('Join result arrived after its deadline');
                    await notifyPythonVoiceStatus(guild.id, true, voiceChannel.id);
                    connection.on(VoiceConnectionStatus.Disconnected, async () => {
                        try {
                            await entersState(connection, VoiceConnectionStatus.Ready, 5000);
                        } catch {
                            if (voiceConnections.get(guildId) !== connection) return;
                            await releaseVoiceConnection(guildId, connection);
                            await notifyPythonVoiceStatus(guildId, false);
                            console.warn('[Voice] Disconnected; stopped reconnecting');
                        }
                    });
                    console.log(`[Voice] Successfully joined voice channel: ${voiceChannel.name}`);
                } catch (error) {
                    console.error(`[Voice] Error joining voice channel:`, error);
                    if (connection && voiceConnections.get(guildId) === connection) {
                        await releaseVoiceConnection(guildId, connection);
                    } else if (connection && connection.state.status !== VoiceConnectionStatus.Destroyed) {
                        connection.destroy();
                    }
                    await report(false, 'Could not establish the voice connection. I stopped trying.')
                        .catch(reportError => console.warn('[Voice] Could not report join result:', reportError.message));
                    await notifyPythonVoiceStatus(guild.id, false);
                }
            } else {
                console.log('[Voice] No voice channel found in guild');
            }
        } else if (command === 'leave') {
            const connection = voiceConnections.get(guild.id);
            if (connection) {
                await releaseVoiceConnection(guildId, connection);
                await notifyPythonVoiceStatus(guild.id, false);
                console.log(`[Voice] Left voice channel in guild ${guildId}`);
            }
        }
    } catch (error) {
        console.warn('[Voice] Command failed in guild', guildId, error.message);
    } finally {
        runningVoiceCommands.delete(guildId);
    }
}

const actionPolls = new Set();
async function pollVoiceActions(guildId, connection) {
    if (actionPolls.has(guildId)) return;
    actionPolls.add(guildId);
    try {
        const { data } = await axios.get(PYTHON_API_URL + '/voice/actions/' + guildId, {
            params: { session_id: voiceSessionId }, timeout: 1800,
        });
        if (!data.action_id) return;
        let outcome;
        try {
            if (voiceConnections.get(guildId) !== connection || connection.joinConfig.channelId !== data.channel_id) {
                outcome = { error: true, detail: 'The voice channel changed.' };
            } else if (data.action === 'deafen') {
                const rejoined = connection.rejoin({ selfDeaf: data.deafened });
                if (!rejoined) {
                    outcome = { error: true, detail: 'Discord could not update the voice connection.' };
                } else {
                    if (data.deafened) {
                        deafenedGuilds.add(guildId);
                        resetCapture(guildId, connection);
                    } else {
                        deafenedGuilds.delete(guildId);
                    }
                    outcome = { detail: data.deafened ? 'Jenny stopped listening.' : 'Jenny is listening again.' };
                }
            } else {
                outcome = { error: true, detail: 'Unknown voice action.' };
            }
        } catch (error) {
            console.warn('[Voice] Voice action failed:', error.message);
            outcome = { error: true, detail: 'Could not finish the voice action.' };
        }
        await axios.post(PYTHON_API_URL + '/voice/actions/' + data.action_id + '/result', {
            ...outcome, guild_id: guildId, session_id: voiceSessionId,
        }, { timeout: 1800 });
    } catch (error) {
        console.warn('[Voice] Voice action check failed:', error.message);
    } finally {
        actionPolls.delete(guildId);
    }
}

// Each guild can poll independently. Do not consume new text during speech playback.
const ttsPolls = new Set();
async function pollGuildTTS(guildId) {
    const state = getTTSState(guildId);
    if (ttsPolls.has(guildId) || guildTurns.has(guildId) || state.isGenerating || state.isPlaying) return;
    ttsPolls.add(guildId);
    try {
        const { data } = await axios.get(PYTHON_API_URL + '/voice/tts/' + guildId, { timeout: 2000 });
        if (data.status === 'success' && data.text) {
            await enqueueGuildTurn(guildId, () => !isInVoiceChannel(guildId), () =>
                playTTSInChunks(guildId, data.text, data.user_id || null));
        }
    } catch (error) {
        console.warn('[Voice] Could not poll speech:', guildId, error.message);
    } finally {
        ttsPolls.delete(guildId);
    }
}

setInterval(() => {
    for (const [guildId, connection] of voiceConnections) {
        if (connection.state.status === VoiceConnectionStatus.Ready) {
            void pollGuildTTS(guildId);
            void pollVoiceActions(guildId, connection);
        }
    }
}, 1000);
client.on(Events.GuildDelete, guild => {
    textChannels.delete(guild.id);
    const connection = voiceConnections.get(guild.id);
    if (connection) void releaseVoiceConnection(guild.id, connection);
});
client.login(DISCORD_TOKEN);
