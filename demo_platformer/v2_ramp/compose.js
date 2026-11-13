// Draws one level from a spec of platforms. The model writes coordinates; this
// program writes the grid. The rules listed in --describe mirror the numbers in
// check_game.js (MIN_PLATFORMS, MIN_SPREAD, MAX_RUN, MAX_GOAL_ROW, MIN_ROUTE_SHARE and the ramp tables
// MIN_CLIMB / MIN_BACK / MIN_FALL / MIN_JUMPS): change them together.
// The program writes the grid, so every row has the same length and S/G always
// stand on '#'.
//   node compose.js --describe   prints what the writer is told about the spec
//   node compose.js              reads the spec on stdin, prints the JSON array of row strings
const W = 60, H = 20;

// Optional: how the check measures difficulty. Used only when the set's briefs are planned
// (not shown to the writer), so the axes that climb are the ones that raise the score.
if (process.argv.includes('--difficulty')) {
  console.log(`Difficulty of a level is one number, built from two parts.
Main part: the hardest jump on the kindest route from start to goal. A jump costs max(columns crossed / 4, rows climbed / 3); a drop of any height costs 0. So the cost runs from 0.25 (a 1-column hop or 1-row step) to 1.0 (a 4-column gap or a 3-row climb), in steps of about 0.083. Wider gaps and taller climbs raise it.
Second part, between levels with the same hardest jump: how much the level asks of the player. It rises with: the rows the player is forced to climb (up to 16), the jumps the player is forced to make against the main direction, so the route doubles back (up to 3), the cost of the worst fall, counted in the jumps needed to climb back to where the player fell from (up to 6), and the jumps on the shortest route above 4 (up to 12 in all).
What does not raise it: more platforms, a taller level, decoys, scenery, or a longer route of easy jumps along one direction, because the kindest route is the one that counts.
Every one of these has a floor that rises with the seed, so the set starts gentle and ends demanding. The 40 levels must not get easier from seed to seed, and the last must be harder than the first.`);
  process.exit(0);
}

if (process.argv.includes('--describe')) {
  console.log(`You are designing one level of a platformer that people play for fun. A good level is a small journey the player wants to finish and then play again.
What makes a level fun:
- The player makes decisions. There are two or more ways to go, some of them dead ends or traps, so reading the level matters.
- It asks for different moves: a quick hop, a long leap, a climb, a big drop. A level that asks for the same jump again and again is dull.
- It has rhythm and shape: a run of similar jumps that sets a pattern, then a break in the pattern; a staircase, a pit, a tower, a bridge, floating islands.
- It is a journey, not a fall. The player climbs to the goal, the route turns back on itself (a zigzag or a switchback), and a missed jump costs real effort to recover from.
- Everything built is part of the game. The shapes are the way to the goal, not scenery beside it: every platform can be reached, and the route runs through most of them. The few that are not on the route are choices, dead ends and traps the player can actually reach.
- It uses the whole screen, high and low, and you can see where the goal is from the start.
- The risky way is shorter or higher, and the safe way is longer.
What makes a level dull, and is rejected: a flat floor, one long slab, a row of identical steps, a corridor with a single path, a few platforms with one bump, a level that runs one way and falls to a goal on the floor, and platforms nobody can reach or the route never needs.
Before the JSON, write one DESIGN line: the idea of this level in one or two sentences (its shape, its main decision, its trick). Then the platform list that builds it.

How the level is described. A program draws the grid from your list, so give coordinates only.
The level is ${W} columns wide (col 0..${W - 1}) and ${H} rows tall (row 0 is the top, row ${H - 1} the bottom).
A platform is a horizontal run of solid cells: [row, first_col, last_col]. Everything else is air.
The player stands on top of a platform. It jumps up at most 3 rows, crosses at most 4 empty columns in one jump (at most 3 when the step up is 3 rows), and can drop any height. Platforms are solid: the player cannot jump up through one or drop through one, only around its ends.
So from one platform to the next: the next top may be at most 3 rows higher, and the gap between them at most 4 columns. A route from start to goal must exist through such jumps.
"start" and "goal" are [row, col] of the cell the player stands in; the cell under it must be on a platform (the program adds it if missing).
The check measures what makes a level good, so meet it in the first reply:
- at least 8 separate platforms of 3 or more cells each (use 12 to 25), and at least 100 solid cells in total;
- platforms are places to stand, not walls of floor: none longer than 30 cells, except on the last 3 rows (rows 17, 18, 19);
- the platforms use the height: their rows span at least 8 rows from the highest to the lowest;
- the goal stands on row 14 or higher (rows 15 to 19 are floor, not where a level ends);
- the shortest route from start to goal takes at least 4 jumps between different platforms (more for later seeds, below), and uses at least 2 different kinds of move (hop, jump or long leap by columns crossed; up, level or down by height), so a shortcut that skips ahead counts against you;
- there are choices: at least 3 platforms the shortest route never stands on, as side paths, dead ends and alternatives;
- every platform can be reached from the start by jumps inside the limits above, and the shortest route stands on at least 40% of the platforms (a shape the route never uses, or no one can reach, is wasted);
- every jump on the route stays inside the limits above;
- two levels written for different seeds must not be the same level.

The ramp. The levels run from gentle to demanding, and the floors below rise with the seed number you are writing for. Seeds come in five bands of 8. Meet at least the floor for your band; going above it is fine. Four measures:
- CLIMB: the rows the player has to go up. Of all routes from start to goal, take the one with the least climbing; add up the rows of each step up (dropping costs nothing). A level that only falls from start to goal climbs 0, so put the goal high and make the route climb to it.
- DOUBLING BACK: the jumps the player has to make against the main direction. Count the platform-to-platform jumps that go left and those that go right on the route that needs the fewest of each; the smaller of the two counts is the measure. A route that only runs left to right scores 0. A zigzag, a switchback or a tower that is climbed one way and then the other scores more.
- FALL COST: take each platform on the shortest route, drop the player off it onto a lower platform, and count the fewest jumps needed to get back onto the platform it fell from. The level's score is the worst such fall. A tower of 5 stacked platforms costs about 5 from the top; a platform that sits one jump above the floor costs 1.
- JUMPS: the platform-to-platform jumps on the shortest route.
Floors by seed:
- seeds 0 to 7: climb 2, doubling back 0, fall cost 1, jumps 4;
- seeds 8 to 15: climb 5, doubling back 1, fall cost 2, jumps 5;
- seeds 16 to 23: climb 8, doubling back 1, fall cost 3, jumps 6;
- seeds 24 to 31: climb 11, doubling back 2, fall cost 4, jumps 7;
- seeds 32 to 39: climb 14, doubling back 2, fall cost 5, jumps 8.
A level that meets its floor and a little more is better than one that only just does. Aim for the numbers in your direction, and build the shape so that every measure rises together: a tower you climb in a zigzag does all four at once.
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
