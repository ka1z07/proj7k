#!/usr/bin/env node
/**
 * tools/lazer-bridge/bridge.js
 *
 * Node.js Realm companion driver for osu!lazer database ingestion in proj7k.
 * Communicates via JSON stdio with the Python RealmBridgeClient.
 */

const fs = require('fs');
const path = require('path');
const Realm = require('realm');

function parseArgs() {
    const args = process.argv.slice(2);
    if (args.length === 0) {
        console.error(JSON.stringify({ success: false, error: 'No command specified' }));
        process.exit(1);
    }

    const command = args[0];
    let realmPath = null;

    for (let i = 1; i < args.length; i++) {
        if (args[i] === '--realm' && i + 1 < args.length) {
            realmPath = args[i + 1];
            i++;
        }
    }

    if (!realmPath) {
        // Fallback default osu!lazer path on macOS
        const homeDir = process.env.HOME || '';
        realmPath = path.join(homeDir, 'Library', 'Application Support', 'osu', 'client.realm');
    }

    return { command, realmPath };
}

function outputJsonAndExit(obj, exitCode = 0) {
    const jsonStr = JSON.stringify(obj) + '\n';
    if (!process.stdout.write(jsonStr)) {
        process.stdout.once('drain', () => process.exit(exitCode));
    } else {
        process.exit(exitCode);
    }
}


function readStdin() {
    return new Promise((resolve, reject) => {
        let data = '';
        process.stdin.setEncoding('utf8');
        process.stdin.on('data', chunk => {
            data += chunk;
        });
        process.stdin.on('end', () => {
            try {
                if (!data.trim()) {
                    resolve({});
                } else {
                    resolve(JSON.parse(data));
                }
            } catch (err) {
                reject(new Error(`Failed to parse stdin JSON: ${err.message}`));
            }
        });
        process.stdin.on('error', err => reject(err));
    });
}

function safeIdToString(val) {
    if (!val) return '';
    if (typeof val === 'string') return val;
    if (typeof val.toHexString === 'function') return val.toHexString();
    if (typeof val.toString === 'function') return val.toString();
    return String(val);
}

function parseUuid(idStr) {
    try {
        return new Realm.BSON.UUID(idStr);
    } catch (e) {
        return idStr;
    }
}

/**
 * Shared helper to mutate beatmap properties in an open write transaction.
 */
function mutateBeatmaps(realm, items) {
    let count = 0;
    for (const item of items) {
        let beatmap = null;
        const uuid = parseUuid(item.id);
        try {
            beatmap = realm.objectForPrimaryKey('Beatmap', uuid);
        } catch (e) {
            const matches = realm.objects('Beatmap').filtered('ID == $0', uuid);
            if (matches.length > 0) beatmap = matches[0];
        }

        if (beatmap) {
            if (typeof item.star_rating === 'number') {
                beatmap.StarRating = item.star_rating;
            }
            if (typeof item.difficulty_name === 'string') {
                beatmap.DifficultyName = item.difficulty_name;
            }
            if (beatmap.Metadata && typeof item.tags === 'string') {
                beatmap.Metadata.Tags = item.tags;
            }
            count++;
        }
    }
    return count;
}

/**
 * Extract physical file hash corresponding to the .osu file in beatmap set.
 */
function resolveFileHash(beatmap) {
    if (beatmap.Hash) {
        return beatmap.Hash;
    }
    if (beatmap.BeatmapSet && beatmap.BeatmapSet.Files) {
        for (const namedUsage of beatmap.BeatmapSet.Files) {
            if (namedUsage.Filename && namedUsage.Filename.endsWith('.osu') && namedUsage.File) {
                return namedUsage.File.Hash || '';
            }
        }
    }
    return '';
}

async function handleStatus(realmPath) {
    let realm;
    let result;
    try {
        realm = new Realm({ path: realmPath, readOnly: true });
        const beatmaps = realm.objects('Beatmap');
        const mania7k = beatmaps.filtered('Ruleset.OnlineID == 3 && Difficulty.CircleSize == 7');
        result = {
            success: true,
            status: 'ok',
            total_beatmaps: beatmaps.length,
            total_7k: mania7k.length,
            realm_path: realmPath
        };
    } catch (err) {
        result = { success: false, error: err.message };
    } finally {
        if (realm && !realm.isClosed) realm.close();
    }
    outputJsonAndExit(result);
}

async function handleDump7k(realmPath) {
    let realm;
    let result;
    try {
        realm = new Realm({ path: realmPath, readOnly: true });
        const mania7k = realm.objects('Beatmap').filtered('Ruleset.OnlineID == 3 && Difficulty.CircleSize == 7');
        
        const records = [];
        for (let i = 0; i < mania7k.length; i++) {
            const b = mania7k[i];
            records.push({
                id: safeIdToString(b.ID),
                hash: b.Hash || '',
                md5_hash: b.MD5Hash || '',
                file_hash: resolveFileHash(b),
                star_rating: typeof b.StarRating === 'number' ? b.StarRating : -1.0,
                difficulty_name: b.DifficultyName || '',
                tags: (b.Metadata && b.Metadata.Tags) ? b.Metadata.Tags : '',
                title: (b.Metadata && b.Metadata.Title) ? b.Metadata.Title : '',
                artist: (b.Metadata && b.Metadata.Artist) ? b.Metadata.Artist : '',
                ruleset_id: (b.Ruleset && typeof b.Ruleset.OnlineID === 'number') ? b.Ruleset.OnlineID : 3,
                circle_size: (b.Difficulty && typeof b.Difficulty.CircleSize === 'number') ? b.Difficulty.CircleSize : 7.0,
            });
        }

        result = {
            success: true,
            beatmaps: records
        };
    } catch (err) {
        result = { success: false, error: err.message };
    } finally {
        if (realm && !realm.isClosed) realm.close();
    }
    outputJsonAndExit(result);
}

async function handleUpdateBatch(realmPath) {
    const input = await readStdin();
    const updates = input.updates || [];
    const collections = input.collections || {};

    let realm;
    let result;
    try {
        realm = new Realm({ path: realmPath, readOnly: false });
        let updatedCount = 0;

        realm.write(() => {
            // 1. Mutate beatmap attributes
            updatedCount = mutateBeatmaps(realm, updates);

            // 2. Synchronize collections
            for (const [colName, targetHashes] of Object.entries(collections)) {
                if (!Array.isArray(targetHashes)) continue;

                let col = null;
                const matches = realm.objects('BeatmapCollection').filtered('Name == $0', colName);
                if (matches.length > 0) {
                    col = matches[0];
                } else {
                    col = realm.create('BeatmapCollection', {
                        ID: new Realm.BSON.UUID(),
                        Name: colName,
                        BeatmapMD5Hashes: [],
                        LastModified: new Date()
                    });
                }

                // Replace collection content with target deduplicated hashes
                col.BeatmapMD5Hashes = Array.from(new Set(targetHashes));
                col.LastModified = new Date();
            }
        });

        result = {
            success: true,
            updated_count: updatedCount
        };
    } catch (err) {
        result = { success: false, error: err.message };
    } finally {
        if (realm && !realm.isClosed) realm.close();
    }
    outputJsonAndExit(result);
}

async function handleRevertBatch(realmPath) {
    const input = await readStdin();
    const reverts = input.reverts || [];
    const collectionsToClean = input.collections_to_clean || [];

    let realm;
    let result;
    try {
        realm = new Realm({ path: realmPath, readOnly: false });
        let revertedCount = 0;

        realm.write(() => {
            // 1. Revert beatmap properties
            revertedCount = mutateBeatmaps(realm, reverts);

            // 2. Clean collections if specified
            for (const colName of collectionsToClean) {
                const matches = realm.objects('BeatmapCollection').filtered('Name == $0', colName);
                if (matches.length > 0) {
                    realm.delete(matches);
                }
            }
        });

        result = {
            success: true,
            updated_count: revertedCount
        };
    } catch (err) {
        result = { success: false, error: err.message };
    } finally {
        if (realm && !realm.isClosed) realm.close();
    }
    outputJsonAndExit(result);
}

async function main() {
    const { command, realmPath } = parseArgs();

    // Unified realm path existence guard
    if (!fs.existsSync(realmPath)) {
        outputJsonAndExit({ success: false, error: `Realm database not found at ${realmPath}` });
        return;
    }

    switch (command) {
        case 'status':
            await handleStatus(realmPath);
            break;
        case 'dump-7k':
            await handleDump7k(realmPath);
            break;
        case 'update-batch':
            await handleUpdateBatch(realmPath);
            break;
        case 'revert-batch':
            await handleRevertBatch(realmPath);
            break;
        default:
            outputJsonAndExit({ success: false, error: `Unknown command: ${command}` }, 1);
    }
}

main().catch(err => {
    outputJsonAndExit({ success: false, error: err.message }, 1);
});

