/**
 * RTD / POT / DI / AO relay mappings.
 * NI line format: P{port}.{line}  e.g. P11.6 → port 11, line 6 (number before dot = port).
 */
window.DAQ_USB_CONFIG = {
    RTD: [
        { channel: 1, title: 'Ry1+ / Ry1-', rows: [
            { ohm: '200', relay: 'K102', net: 'RTD_1', pin: '4', niLine: 'P11.6' },
            { ohm: '150', relay: 'K103', net: 'RTD_2', pin: '2', niLine: 'P11.7' },
            { ohm: '120', relay: 'K104', net: 'RTD_3', pin: '1', niLine: 'P8.7' },
            { ohm: '100', relay: 'K105', net: 'RTD_4', pin: '3', niLine: 'P8.6' },
            { ohm: '68', relay: 'K106', net: 'RTD_5', pin: '5', niLine: 'P8.5' },
        ]},
        { channel: 2, title: 'Ry2+ / Ry2-', rows: [
            { ohm: '200', relay: 'K107', net: 'RTD_6', pin: '7', niLine: 'P8.4' },
            { ohm: '150', relay: 'K108', net: 'RTD_7', pin: '9', niLine: 'P8.3' },
            { ohm: '120', relay: 'K109', net: 'RTD_8', pin: '11', niLine: 'P8.2' },
            { ohm: '100', relay: 'K110', net: 'RTD_9', pin: '13', niLine: 'P8.1' },
            { ohm: '68', relay: 'K111', net: 'RTD_10', pin: '15', niLine: 'P8.0' },
        ]},
        { channel: 3, title: 'Ry3+ / Ry3-', rows: [
            { ohm: '200', relay: 'K112', net: 'RTD_11', pin: '17', niLine: 'P7.7' },
            { ohm: '150', relay: 'K113', net: 'RTD_12', pin: '19', niLine: 'P7.6' },
            { ohm: '120', relay: 'K114', net: 'RTD_13', pin: '21', niLine: 'P7.5' },
            { ohm: '100', relay: 'K115', net: 'RTD_14', pin: '23', niLine: 'P7.4' },
            { ohm: '68', relay: 'K116', net: 'RTD_15', pin: '25', niLine: 'P7.3' },
        ]},
        { channel: 4, title: 'Ry4+ / Ry4-', rows: [
            { ohm: '200', relay: 'K117', net: 'RTD_16', pin: '27', niLine: 'P7.2' },
            { ohm: '150', relay: 'K118', net: 'RTD_17', pin: '29', niLine: 'P7.1' },
            { ohm: '120', relay: 'K119', net: 'RTD_18', pin: '31', niLine: 'P7.0' },
            { ohm: '100', relay: 'K120', net: 'RTD_19', pin: '33', niLine: 'P6.7' },
            { ohm: '68', relay: 'K121', net: 'RTD_20', pin: '35', niLine: 'P6.6' },
        ]},
        { channel: 5, title: 'Ry5+ / Ry5-', rows: [
            { ohm: '200', relay: 'K122', net: 'RTD_21', pin: '37', niLine: 'P6.5' },
            { ohm: '150', relay: 'K123', net: 'RTD_22', pin: '39', niLine: 'P6.4' },
            { ohm: '120', relay: 'K124', net: 'RTD_23', pin: '41', niLine: 'P6.3' },
            { ohm: '100', relay: 'K125', net: 'RTD_24', pin: '43', niLine: 'P6.2' },
            { ohm: '68', relay: 'K126', net: 'RTD_25', pin: '45', niLine: 'P6.1' },
        ]},
        { channel: 6, title: 'Ry6+ / Ry6-', rows: [
            { ohm: '200', relay: 'K127', net: 'RTD_26', pin: '47', niLine: 'P6.0' },
            { ohm: '150', relay: 'K128', net: 'RTD_27', pin: '—', niLine: 'P2.7' },
            { ohm: '120', relay: 'K129', net: 'RTD_28', pin: '—', niLine: 'P2.6' },
            { ohm: '100', relay: 'K130', net: 'RTD_29', pin: '—', niLine: 'P5.7' },
            { ohm: '68', relay: 'K131', net: 'RTD_30', pin: '—', niLine: 'P5.6' },
        ]},
    ],
    POT: [
        { channel: 1, title: 'Rx1+ / Rx1- (J22)', rows: [
            { ohm: '20K', relay: 'K65', net: 'POT_1', pin: 'J22-41', niLine: 'P0.3' },
            { ohm: '10K', relay: 'K66', net: 'POT_2', pin: 'J22-42', niLine: 'P3.3' },
            { ohm: '5K', relay: 'K67', net: 'POT_3', pin: 'J22-43', niLine: 'P0.2' },
            { ohm: '1K', relay: 'K68', net: 'POT_4', pin: 'J22-44', niLine: 'P3.2' },
            { ohm: '470R', relay: 'K69', net: 'POT_5', pin: 'J22-45', niLine: 'P0.1' },
        ]},
        { channel: 2, title: 'Rx2+ / Rx2-', rows: [
            { ohm: '20K', relay: 'K70', net: 'POT_6', pin: 'J22-46', niLine: 'P3.1' },
            { ohm: '10K', relay: 'K71', net: 'POT_7', pin: 'J22-47', niLine: 'P0.0' },
            { ohm: '5K', relay: 'K72', net: 'POT_8', pin: 'J22-48', niLine: 'P3.0' },
            { ohm: '1K', relay: 'K73', net: 'POT_9', pin: 'J23-38', niLine: 'P9.5' },
            { ohm: '470R', relay: 'K74', net: 'POT_10', pin: 'J23-40', niLine: 'P9.4' },
        ]},
        { channel: 3, title: 'Rx3+ / Rx3-', rows: [
            { ohm: '20K', relay: 'K75', net: 'POT_11', pin: 'J23-42', niLine: 'P9.3' },
            { ohm: '10K', relay: 'K76', net: 'POT_12', pin: 'J23-44', niLine: 'P9.2' },
            { ohm: '5K', relay: 'K77', net: 'POT_13', pin: 'J23-46', niLine: 'P9.1' },
            { ohm: '1K', relay: 'K78', net: 'POT_14', pin: 'J23-48', niLine: 'P9.0' },
            { ohm: '470R', relay: 'K79', net: 'POT_15', pin: 'J23-24', niLine: 'P10.4' },
        ]},
        { channel: 4, title: 'Rx4+ / Rx4-', rows: [
            { ohm: '20K', relay: 'K80', net: 'POT_16', pin: 'J23-26', niLine: 'P10.3' },
            { ohm: '10K', relay: 'K81', net: 'POT_17', pin: 'J23-28', niLine: 'P10.2' },
            { ohm: '5K', relay: 'K82', net: 'POT_18', pin: 'J23-30', niLine: 'P10.1' },
            { ohm: '1K', relay: 'K83', net: 'POT_19', pin: 'J23-32', niLine: 'P10.0' },
            { ohm: '470R', relay: 'K84', net: 'POT_20', pin: 'J23-34', niLine: 'P9.7' },
        ]},
        { channel: 5, title: 'Rx5+ / Rx5-', rows: [
            { ohm: '20K', relay: 'K85', net: 'POT_21', pin: 'J23-36', niLine: 'P9.6' },
            { ohm: '10K', relay: 'K86', net: 'POT_22', pin: 'J23-10', niLine: 'P11.3' },
            { ohm: '5K', relay: 'K87', net: 'POT_23', pin: 'J23-12', niLine: 'P11.2' },
            { ohm: '1K', relay: 'K88', net: 'POT_24', pin: 'J23-14', niLine: 'P11.1' },
            { ohm: '470R', relay: 'K89', net: 'POT_25', pin: 'J23-16', niLine: 'P11.0' },
        ]},
        { channel: 6, title: 'Rx6+ / Rx6-', rows: [
            { ohm: '20K', relay: 'K90', net: 'POT_26', pin: 'J23-18', niLine: 'P10.7' },
            { ohm: '10K', relay: 'K91', net: 'POT_27', pin: 'J23-20', niLine: 'P10.6' },
            { ohm: '5K', relay: 'K92', net: 'POT_28', pin: 'J23-22', niLine: 'P10.5' },
            { ohm: '1K', relay: 'K93', net: 'POT_29', pin: 'J23-6', niLine: 'P11.5' },
            { ohm: '470R', relay: 'K94', net: 'POT_30', pin: 'J23-8', niLine: 'P11.4' },
        ]},
    ],
    DI_DRY: [{
        title: 'DI — Dry section',
        rows: [
            { ch: 1, relay: 'K17', net: 'DI_1', pin: '1', niLine: 'P2.7' },
            { ch: 2, relay: 'K18', net: 'DI_2', pin: '2', niLine: 'P5.7' },
            { ch: 3, relay: 'K19', net: 'DI_3', pin: '3', niLine: 'P2.6' },
            { ch: 4, relay: 'K20', net: 'DI_4', pin: '4', niLine: 'P5.6' },
            { ch: 5, relay: 'K21', net: 'DI_5', pin: '5', niLine: 'P2.5' },
            { ch: 6, relay: 'K22', net: 'DI_6', pin: '6', niLine: 'P5.5' },
            { ch: 7, relay: 'K23', net: 'DI_7', pin: '7', niLine: 'P2.4' },
            { ch: 8, relay: 'K24', net: 'DI_8', pin: '8', niLine: 'P5.4' },
            { ch: 9, relay: 'K25', net: 'DI_9', pin: '9', niLine: 'P2.3' },
            { ch: 10, relay: 'K26', net: 'DI_10', pin: '10', niLine: 'P5.3' },
            { ch: 11, relay: 'K27', net: 'DI_11', pin: '11', niLine: 'P2.2' },
            { ch: 12, relay: 'K28', net: 'DI_12', pin: '12', niLine: 'P5.2' },
        ],
    }],
    DI_WET: [{
        title: 'DI — Wet section',
        rows: [
            { ch: 1, relay: 'K41', net: 'DI_13', pin: '13', niLine: 'P2.1' },
            { ch: 2, relay: 'K42', net: 'DI_14', pin: '14', niLine: 'P5.1' },
            { ch: 3, relay: 'K43', net: 'DI_15', pin: '15', niLine: 'P2.0' },
            { ch: 4, relay: 'K44', net: 'DI_16', pin: '16', niLine: 'P5.0' },
            { ch: 5, relay: 'K45', net: 'DI_17', pin: '17', niLine: 'P1.7' },
            { ch: 6, relay: 'K46', net: 'DI_18', pin: '18', niLine: 'P4.7' },
            { ch: 7, relay: 'K47', net: 'DI_19', pin: '19', niLine: 'P1.6' },
            { ch: 8, relay: 'K48', net: 'DI_20', pin: '20', niLine: 'P4.6' },
            { ch: 9, relay: 'K49', net: 'DI_21', pin: '21', niLine: 'P1.5' },
            { ch: 10, relay: 'K50', net: 'DI_22', pin: '22', niLine: 'P4.5' },
            { ch: 11, relay: 'K51', net: 'DI_23', pin: '23', niLine: 'P1.4' },
            { ch: 12, relay: 'K52', net: 'DI_24', pin: '24', niLine: 'P4.4' },
        ],
    }],
    AO_MAP: [{
        title: 'Analog output relay section',
        rows: [
            { ch: 1, relay: 'K1', net: 'AO_1', pin: '25', niLine: 'P1.3' },
            { ch: 2, relay: 'K2', net: 'AO_2', pin: '26', niLine: 'P4.3' },
            { ch: 3, relay: 'K3', net: 'AO_3', pin: '27', niLine: 'P1.2' },
            { ch: 4, relay: 'K4', net: 'AO_4', pin: '28', niLine: 'P4.2' },
            { ch: 5, relay: 'K5', net: 'AO_5', pin: '29', niLine: 'P1.1' },
            { ch: 6, relay: 'K6', net: 'AO_6', pin: '30', niLine: 'P4.1' },
            { ch: 7, relay: 'K7', net: 'AO_7', pin: '31', niLine: 'P1.0' },
            { ch: 8, relay: 'K8', net: 'AO_8', pin: '32', niLine: 'P4.0' },
            { ch: 9, relay: 'K9', net: 'AO_9', pin: '33', niLine: 'P0.7' },
            { ch: 10, relay: 'K10', net: 'AO_10', pin: '34', niLine: 'P3.7' },
            { ch: 11, relay: 'K11', net: 'AO_11', pin: '35', niLine: 'P0.6' },
            { ch: 12, relay: 'K12', net: 'AO_12', pin: '36', niLine: 'P3.6' },
            { ch: 13, relay: 'K13', net: 'AO_13', pin: '37', niLine: 'P0.5' },
            { ch: 14, relay: 'K14', net: 'AO_14', pin: '38', niLine: 'P3.5' },
            { ch: 15, relay: 'K15', net: 'AO_15', pin: '39', niLine: 'P0.4' },
            { ch: 16, relay: 'K16', net: 'AO_16', pin: '40', niLine: 'P3.4' },
        ],
    }],
};

/** P11.6 → { port: 11, line: 6 } — digits before dot = port, after dot = line */
window.parseNiLine = function parseNiLine(niLine) {
    const m = String(niLine || '').trim().match(/^P(\d+)\.(\d+)$/i);
    if (!m) return null;
    return { port: parseInt(m[1], 10), line: parseInt(m[2], 10) };
};

window.lineKey = function lineKey(port, line) {
    return `${port}.${line}`;
};

window.parseLineKey = function parseLineKey(key) {
    const parts = String(key).split('.');
    if (parts.length !== 2) return null;
    return { port: parseInt(parts[0], 10), line: parseInt(parts[1], 10) };
};

window.niLineToChannel = function niLineToChannel(deviceName, niLine) {
    const p = parseNiLine(niLine);
    if (!p) return null;
    return `${deviceName}/port${p.port}/line${p.line}`;
};

window.resolveNiChannel = function resolveNiChannel(deviceName, doChannels, niLine) {
    const p = parseNiLine(niLine);
    if (!p) return null;
    const suffix = `/port${p.port}/line${p.line}`.toLowerCase();
    const list = doChannels || [];
    const hit = list.find(ch => String(ch).toLowerCase().endsWith(suffix));
    return hit || niLineToChannel(deviceName, niLine);
};

window.linesStorageKey = function linesStorageKey(deviceName) {
    return `daq_selected_lines_${deviceName}`;
};

window.getSavedLines = function getSavedLines(deviceName) {
    try {
        const raw = localStorage.getItem(linesStorageKey(deviceName));
        if (!raw) return null;
        const arr = JSON.parse(raw);
        if (!Array.isArray(arr)) return null;
        return arr.map(String);
    } catch {
        return null;
    }
};

window.saveSelectedLines = function saveSelectedLines(deviceName, lineKeys) {
    localStorage.setItem(linesStorageKey(deviceName), JSON.stringify(lineKeys.sort()));
};

window.allLineKeys = function allLineKeys() {
    const keys = [];
    for (let p = 0; p < 12; p++) {
        for (let l = 0; l < 8; l++) keys.push(lineKey(p, l));
    }
    return keys;
};

window.groupChannelsByPort = function groupChannelsByPort(channels) {
    const byPort = {};
    for (const ch of channels) {
        const m = ch.match(/port(\d+)\/line(\d+)/i);
        if (!m) continue;
        const pn = parseInt(m[1], 10);
        const ln = parseInt(m[2], 10);
        if (!byPort[pn]) byPort[pn] = [];
        byPort[pn].push({ channel: ch, line: ln, key: lineKey(pn, ln) });
    }
    for (const pn of Object.keys(byPort)) {
        byPort[pn].sort((a, b) => a.line - b.line);
    }
    return byPort;
};

window.filterChannelsBySavedLines = function filterChannelsBySavedLines(channels, deviceName) {
    const allByPort = groupChannelsByPort(channels);
    const saved = getSavedLines(deviceName);
    const active = new Set(saved && saved.length ? saved : allLineKeys());
    const byPort = {};
    const out = [];
    const portNums = [];
    for (let p = 0; p < 12; p++) {
        const portLines = (allByPort[p] || []).filter(item => active.has(item.key));
        if (portLines.length) {
            byPort[p] = portLines;
            portNums.push(p);
            out.push(...portLines.map(item => item.channel));
        }
    }
    return { byPort, portNums, channels: out, active };
};

window.diLinesStorageKey = function diLinesStorageKey(deviceName, moduleName) {
    return `daq_selected_di_${deviceName}_${moduleName}`;
};

window.getSavedDiLines = function getSavedDiLines(deviceName, moduleName) {
    try {
        const raw = localStorage.getItem(diLinesStorageKey(deviceName, moduleName));
        if (!raw) return null;
        const arr = JSON.parse(raw);
        if (!Array.isArray(arr)) return null;
        return arr.map(String);
    } catch {
        return null;
    }
};

window.saveSelectedDiLines = function saveSelectedDiLines(deviceName, moduleName, lineKeys) {
    localStorage.setItem(
        diLinesStorageKey(deviceName, moduleName),
        JSON.stringify(lineKeys.sort())
    );
};

window.filterDiChannelsBySavedLines = function filterDiChannelsBySavedLines(
    diChannels, deviceName, moduleName
) {
    const allByPort = groupChannelsByPort(diChannels || []);
    const portNums = Object.keys(allByPort).map(Number).sort((a, b) => a - b);
    const allKeys = [];
    for (const pn of portNums) {
        for (const item of allByPort[pn]) allKeys.push(item.key);
    }
    const saved = getSavedDiLines(deviceName, moduleName);
    const active = new Set(saved && saved.length ? saved : allKeys);
    const byPort = {};
    const out = [];
    const filteredPortNums = [];
    for (const pn of portNums) {
        const portLines = (allByPort[pn] || []).filter(item => active.has(item.key));
        if (portLines.length) {
            byPort[pn] = portLines;
            filteredPortNums.push(pn);
            out.push(...portLines.map(item => item.channel));
        }
    }
    return { byPort, portNums: filteredPortNums, channels: out, active };
};

/** Sort DI channels and group into relay pairs: global line0+1, 2+3, … */
window.buildDiRelayPairs = function buildDiRelayPairs(diChannels) {
    const items = (diChannels || []).map((ch, idx) => {
        const m = String(ch).match(/port(\d+)\/line(\d+)/i);
        return {
            channel: ch,
            channelIndex: idx,
            port: m ? parseInt(m[1], 10) : 0,
            line: m ? parseInt(m[2], 10) : idx,
        };
    }).sort((a, b) => (a.port !== b.port ? a.port - b.port : a.line - b.line));

    items.forEach((item, globalLine) => {
        item.globalLine = globalLine;
    });

    const pairs = [];
    for (let i = 0; i < items.length; i += 2) {
        pairs.push({
            relayNum: Math.floor(i / 2) + 1,
            line0: items[i],
            line1: items[i + 1] || null,
        });
    }
    return pairs;
};

window.relayPairLineKeys = function relayPairLineKeys(pair) {
    const keys = [];
    if (pair?.line0) keys.push(lineKey(pair.line0.port, pair.line0.line));
    if (pair?.line1) keys.push(lineKey(pair.line1.port, pair.line1.line));
    return keys;
};

window.isRelayPairInSet = function isRelayPairInSet(keySet, pair) {
    const keys = relayPairLineKeys(pair);
    return keys.length > 0 && keys.every(k => keySet.has(k));
};

window.filterDiRelayPairsBySaved = function filterDiRelayPairsBySaved(
    diChannels, deviceName, moduleName
) {
    const pairs = buildDiRelayPairs(diChannels);
    const allKeys = pairs.flatMap(relayPairLineKeys);
    const saved = getSavedDiLines(deviceName, moduleName);
    const active = new Set(saved && saved.length ? saved : allKeys);
    return pairs.filter(p => isRelayPairInSet(active, p));
};
