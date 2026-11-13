// Draws one level from a spec of platforms. The model writes coordinates; this
// program writes the grid. The rules listed in --describe mirror the numbers in
// check_game.js (MIN_PLATFORMS, MIN_JUMPS, MIN_SPREAD, MAX_RUN): change them together.
// The program writes the grid, so every row has the same length and S/G always
// stand on '#'.
//   node compose.js --describe   prints what the writer is told about the spec
//   node compose.js              reads the spec on stdin, prints the JSON array of row strings
const W = 60, H = 20;

// Optional: how the check measures difficulty. Used only when the set's briefs are planned
// (not shown to the writer), so the axes that climb are the ones that raise the score.
if (process.argv.includes('--difficulty')) {
  console.log(`Difficulty of a level is one number: the hardest jump on the kindest route from start to goal, plus 0.001 for each jump on that route that is that hard.
A jump costs max(columns crossed / 4, rows climbed / 3); a drop of any height costs 0. So the cost runs from 0.25 (a 1-column hop or 1-row step) to 1.0 (a 4-column gap or a 3-row climb), in steps of about 0.083.
What raises difficulty: wider gaps, taller climbs, and a route with no easier alternative. What does not: more platforms, a taller level, decoys, or a longer route of easy jumps, because the kindest route is the one that counts.
The 40 levels must not get easier from seed to seed, and the last must be harder than the first.`);
  process.exit(0);
}

if (process.argv.includes('--describe')) {
  console.log(`You are designing one level of a platformer that people play for fun. A good level is a small journey the player wants to finish and then play again.
What makes a level fun:
- The player makes decisions. There are two or more ways to go, some of them dead ends or traps, so reading the level matters.
- It asks for different moves: a quick hop, a long leap, a climb, a big drop. A level that asks for the same jump again and again is dull.
- It has rhythm and shape: a run of similar jumps that sets a pattern, then a break in the pattern; a staircase, a pit, a tower, a bridge, floating islands.
- It uses the whole screen, high and low, and you can see where the goal is from the start.
- The risky way is shorter or higher, and the safe way is longer.
What makes a level dull, and is rejected: a flat floor, one long slab, a row of identical steps, a corridor with a single path, or a few platforms with one bump.
Before the JSON, write one DESIGN line: the idea of this level in one or two sentences (its shape, its main decision, its trick). Then the platform list that builds it.

How the level is described. A program draws the grid from your list, so give coordinates only.
The level is ${W} columns wide (col 0..${W - 1}) and ${H} rows tall (row 0 is the top, row ${H - 1} the bottom).
A platform is a horizontal run of solid cells: [row, first_col, last_col]. Everything else is air.
The player stands on top of a platform. It jumps up at most 3 rows, crosses at most 4 empty columns in one jump, and can drop any height.
So from one platform to the next: the next top may be at most 3 rows higher, and the gap between them at most 4 columns. A route from start to goal must exist through such jumps.
"start" and "goal" are [row, col] of the cell the player stands in; the cell under it must be on a platform (the program adds it if missing).
The check measures what makes a level good, so meet it in the first reply:
- at least 8 separate platforms of 3 or more cells each (use 12 to 25), and at least 100 solid cells in total;
- platforms are places to stand, not walls of floor: none longer than 30 cells, except on the last 3 rows (rows 17, 18, 19);
- the platforms use the height: their rows span at least 8 rows from the highest to the lowest;
- the shortest route from start to goal takes at least 4 jumps between different platforms, and uses at least 2 different kinds of move (hop, jump or long leap by columns crossed; up, level or down by height), so a shortcut that skips ahead counts against you;
- there are choices: at least 3 platforms the shortest route never stands on, as side paths, dead ends and alternatives;
- every jump on the route stays inside the limits above;
- two levels written for different seeds must not be the same level.
Reply with the DESIGN line, then one JSON object: {"platforms": [[row, c0, c1], ...], "start": [row, col], "goal": [row, col]}`);
  process.exit(0);
}

let input = '';
process.stdin.on('data', d => input += d);
process.stdin.on('end', () => {
  const fail = msg => { console.log(msg); process.exit(1); };
  const m = input.match(/\{\s*"platforms"[\s\S]*\}/) || input.match(/\{[\s\S]*\}/);
  if (!m) fail('the reply holds no JSON object');
  let spec;
  try { spec = JSON.parse(m[0]); } catch (e) { fail('the JSON object does not parse: ' + e.message); }
  if (!Array.isArray(spec.platforms) || !spec.platforms.length) fail('"platforms" must be a non-empty list of [row, first_col, last_col]');
  for (const k of ['start', 'goal']) {
    const p = spec[k];
    if (!Array.isArray(p) || p.length !== 2 || !p.every(Number.isInteger)) fail('"' + k + '" must be [row, col] of two integers');
    if (p[0] < 0 || p[0] >= H - 1 || p[1] < 0 || p[1] >= W) fail('"' + k + '" ' + JSON.stringify(p) + ' is outside rows 0..' + (H - 2) + ' and columns 0..' + (W - 1));
  }
  const g = Array.from({ length: H }, () => Array(W).fill('.'));
  for (const p of spec.platforms) {
    if (!Array.isArray(p) || p.length !== 3 || !p.every(Number.isInteger)) fail('platform ' + JSON.stringify(p) + ' is not [row, first_col, last_col]');
    const [r, a, b] = p;
    if (r < 0 || r >= H) fail('platform ' + JSON.stringify(p) + ' has a row outside 0..' + (H - 1));
    for (let c = Math.max(0, Math.min(a, b)); c <= Math.min(W - 1, Math.max(a, b)); c++) g[r][c] = '#';
  }
  for (const [k, ch] of [['start', 'S'], ['goal', 'G']]) {
    const [r, c] = spec[k];
    g[r][c] = ch;
    g[r + 1][c] = '#';
  }
  console.log(JSON.stringify(g.map(r => r.join(''))));
});
