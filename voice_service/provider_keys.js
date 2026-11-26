'use strict';

/** Read keys in numeric order, with the legacy primary first and backup last. */
function configuredKeys(base, environment = process.env) {
    const prefix = base + '_';
    const numbered = Object.keys(environment)
        .filter(name => name.startsWith(prefix) && /^[1-9][0-9]*$/.test(name.slice(prefix.length)))
        .sort((left, right) => left.length - right.length || (left < right ? -1 : left > right ? 1 : 0));
    const names = [base, ...numbered, base + '_BACKUP'];
    const values = names.map(name => environment[name]?.trim()).filter(Boolean);
    return [...new Set(values)];
}

module.exports = { configuredKeys };
