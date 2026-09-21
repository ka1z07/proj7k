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
    let onlineId = null;
    let fileHash = null;
    let setId = null;
    let userName = null;

    for (let i = 1; i < args.length; i++) {
        if (args[i] === '--realm' && i + 1 < args.length) {
            realmPath = args[i + 1];
            i++;
        } else if (args[i] === '--online-id' && i + 1 < args.length) {
            onlineId = args[i + 1];
            i++;
        } else if (args[i] === '--hash' && i + 1 < args.length) {
            fileHash = args[i + 1];
            i++;
        } else if (args[i] === '--set-id' && i + 1 < args.length) {
            setId = args[i + 1];
            i++;
        } else if (args[i] === '--user' && i + 1 < args.length) {
            userName = args[i + 1];
            i++;
        }
    }

    if (!realmPath) {
        // Fallback default osu!lazer path on macOS
        const homeDir = process.env.HOME || '';
        realmPath = path.join(homeDir, 'Library', 'Application Support', 'osu', 'client.realm');
    }

    return { command, realmPath, onlineId, fileHash, setId, userName };
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

function resolveNamedFile(beatmap, extensions) {
    if (beatmap.BeatmapSet && beatmap.BeatmapSet.Files) {
        for (const namedUsage of beatmap.BeatmapSet.Files) {
            const fname = (namedUsage.Filename || '').toLowerCase();
            for (const ext of extensions) {
                if (fname.endsWith(ext) && namedUsage.File && namedUsage.File.Hash) {
                    return { filename: namedUsage.Filename, hash: namedUsage.File.Hash };
                }
            }
        }
    }
    return null;
}

function resolveSetFiles(beatmap) {
    const files = [];
    if (beatmap.BeatmapSet && beatmap.BeatmapSet.Files) {
        for (const f of beatmap.BeatmapSet.Files) {
            files.push({
                filename: f.Filename || '',
                hash: (f.File && f.File.Hash) || ''
            });
        }
    }
    return files;
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

        const colObjects = realm.objects('BeatmapCollection');
        const collections = {};
        for (let i = 0; i < colObjects.length; i++) {
            const col = colObjects[i];
            collections[col.Name] = Array.from(col.BeatmapMD5Hashes || []);
        }

        result = {
            success: true,
            beatmaps: records,
            collections: collections
        };
    } catch (err) {
        result = { success: false, error: err.message };
    } finally {
        if (realm && !realm.isClosed) realm.close();
    }
    outputJsonAndExit(result);
}

async function handleDump7kScores(realmPath, targetUser) {
    let realm;
    let result;
    try {
        realm = new Realm({ path: realmPath, readOnly: true });
        const scores = realm.objects('Score');
        const records = [];
        const normTarget = targetUser ? targetUser.trim().toLowerCase() : null;

        for (let i = 0; i < scores.length; i++) {
            const s = scores[i];
            const uName = (s.User && s.User.Username) ? s.User.Username : '';
            if (normTarget && uName.trim().toLowerCase() !== normTarget) {
                continue;
            }

            // Check 7K Mania
            const bm = s.BeatmapInfo;
            if (!bm || !bm.Ruleset || bm.Ruleset.OnlineID !== 3) continue;
            if (!bm.Difficulty || Math.abs((bm.Difficulty.CircleSize || 0) - 7.0) > 0.01) continue;

            // Locate .osr file in s.Files
            let replayHash = '';
            if (s.Files) {
                for (const f of s.Files) {
                    if (f.Filename && f.Filename.toLowerCase().endsWith('.osr') && f.File && f.File.Hash) {
                        replayHash = f.File.Hash;
                        break;
                    }
                }
            }
            if (!replayHash) continue;

            const bHash = resolveFileHash(bm) || bm.Hash || s.BeatmapHash || '';
            if (!bHash) continue;

            records.push({
                player_name: uName || 'Unknown',
                beatmap_file_hash: bHash,
                replay_file_hash: replayHash,
                date: s.Date ? s.Date.toISOString() : '',
                total_score: typeof s.TotalScore === 'number' ? s.TotalScore : 0,
                accuracy: typeof s.Accuracy === 'number' ? s.Accuracy : 0.0,
                rank: typeof s.Rank === 'number' ? s.Rank : -1,
                star_rating: (bm && typeof bm.StarRating === 'number') ? bm.StarRating : 0.0,
                title: (bm && bm.Metadata && bm.Metadata.Title) ? bm.Metadata.Title : '',
                difficulty_name: (bm && bm.DifficultyName) ? bm.DifficultyName : ''
            });
        }

        result = {
            success: true,
            scores: records,
            total_found: records.length
        };
    } catch (err) {
        result = { success: false, error: err.message };
    } finally {
        if (realm && !realm.isClosed) realm.close();
    }
    outputJsonAndExit(result);
}

async function handleDumpCollections(realmPath) {
    let realm;
    let result;
    try {
        realm = new Realm({ path: realmPath, readOnly: true });
        const colObjects = realm.objects('BeatmapCollection');
        const collections = {};
        for (let i = 0; i < colObjects.length; i++) {
            const col = colObjects[i];
            collections[col.Name] = Array.from(col.BeatmapMD5Hashes || []);
        }
        result = {
            success: true,
            collections: collections
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

async function handleLocateBeatmap(realmPath, onlineId, fileHash, setId) {
    let realm;
    let result;
    try {
        realm = new Realm({ path: realmPath, readOnly: true });
        let matches = [];
        if (onlineId) {
            matches = realm.objects('Beatmap').filtered('OnlineID == $0', Number(onlineId));
            if (matches.length === 0) {
                // Secondary check: inspect candidate .osu headers for BeatmapID
                const idTag = `BeatmapID:${onlineId}`;
                const idTagSpace = `BeatmapID: ${onlineId}`;
                const allBms = realm.objects('Beatmap');
                for (let i = 0; i < allBms.length; i++) {
                    const b = allBms[i];
                    const fHash = resolveFileHash(b);
                    if (!fHash || fHash.length < 2) continue;
                    const fPath = path.join(path.dirname(realmPath), 'files', fHash[0], fHash.slice(0, 2), fHash);
                    if (fs.existsSync(fPath)) {
                        try {
                            const buf = Buffer.alloc(3000);
                            const fd = fs.openSync(fPath, 'r');
                            const bytesRead = fs.readSync(fd, buf, 0, 3000, 0);
                            fs.closeSync(fd);
                            const text = buf.toString('utf8', 0, bytesRead);
                            if (text.includes(idTag) || text.includes(idTagSpace)) {
                                matches = [b];
                                break;
                            }
                        } catch (e) {}
                    }
                }
            }
        } else if (fileHash) {
            matches = realm.objects('Beatmap').filtered('MD5Hash == $0 || Hash == $0', fileHash);
        } else if (setId) {
            matches = realm.objects('Beatmap').filtered('BeatmapSet.OnlineID == $0', Number(setId));
        }

        if (matches.length === 0) {
            result = { success: true, found: false };
        } else {
            const b = matches[0];
            const audioFile = resolveNamedFile(b, ['.mp3', '.ogg', '.wav']);
            const bgFile = resolveNamedFile(b, ['.jpg', '.jpeg', '.png']);
            result = {
                success: true,
                found: true,
                beatmap: {
                    id: safeIdToString(b.ID),
                    online_id: b.OnlineID,
                    set_online_id: (b.BeatmapSet && b.BeatmapSet.OnlineID) || null,
                    title: (b.Metadata && b.Metadata.Title) ? b.Metadata.Title : '',
                    artist: (b.Metadata && b.Metadata.Artist) ? b.Metadata.Artist : '',
                    creator: (b.Metadata && b.Metadata.Author && b.Metadata.Author.Username) ? b.Metadata.Author.Username : '',
                    difficulty_name: b.DifficultyName || '',
                    star_rating: typeof b.StarRating === 'number' ? b.StarRating : -1.0,
                    ruleset_id: (b.Ruleset && typeof b.Ruleset.OnlineID === 'number') ? b.Ruleset.OnlineID : 3,
                    circle_size: (b.Difficulty && typeof b.Difficulty.CircleSize === 'number') ? b.Difficulty.CircleSize : 7.0,
                    hash: b.Hash || '',
                    md5_hash: b.MD5Hash || '',
                    osu_file_hash: resolveFileHash(b),
                    audio_file: audioFile,
                    bg_file: bgFile,
                    files: resolveSetFiles(b)
                }
            };
        }
    } catch (err) {
        result = { success: false, error: err.message };
    } finally {
        if (realm && !realm.isClosed) realm.close();
    }
    outputJsonAndExit(result);
}

async function main() {
    const { command, realmPath, onlineId, fileHash, setId, userName } = parseArgs();

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
        case 'dump-7k-scores':
            await handleDump7kScores(realmPath, userName);
            break;
        case 'dump-collections':
            await handleDumpCollections(realmPath);
            break;
        case 'locate-beatmap':
            await handleLocateBeatmap(realmPath, onlineId, fileHash, setId);
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

