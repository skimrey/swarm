// Draws one level from a spec of platforms. The model writes coordinates; this
// program writes the grid. The rules listed in --describe mirror the numbers in
// check_game.js (MIN_PLATFORMS, MIN_SPREAD, MAX_RUN, MAX_GOAL_ROW, MIN_ROUTE_SHARE and the ramp tables
// MIN_CLIMB / MIN_BACK / MIN_FALL / MIN_JUMPS): change them together.
// The program writes the grid, so every row has the same length and S/G always
// stand on '#'.
//   node compose.js --describe   prints what the writer is told about the spec
//   node compose.js              reads the spec on stdin, prints the JSON array of row strings
const W = 60, H = 20;
// The numbers below are the same as check_game.js (TIER_RISE, TIER_REACH, MAX_WIDTH, MIN_DECISIONS, MIN_BAIT, MIN_BRANCH, GREEDY_MUST_FAIL)
// and physics.js TIERS. The rules text is built from them here so the writer is told what the check measures.
const TIER_RISE = [2, 3, 3, 3, 4], TIER_REACH = [4, 4, 5, 6, 6], MAX_WIDTH = [14, 11, 9, 7, 6];
const MIN_DECISIONS = [1, 2, 2, 3, 3], MIN_BAIT = [0, 1, 1, 2, 2], MIN_BRANCH = 2;
const tierLines = TIER_RISE.map((r, t) => `- seeds ${t * 8} to ${t * 8 + 7}: a jump climbs at most ${r} rows and moves at most ${TIER_REACH[t]} columns from the cell it leaves to the cell it lands on (a gap of ${TIER_REACH[t] - 1} empty columns); platforms at most ${MAX_WIDTH[t]} cells long`).join('\n');
const mazeLines = MIN_DECISIONS.map((d, t) => `- seeds ${t * 8} to ${t * 8 + 7}: at least ${d} place${d > 1 ? 's' : ''} to choose, at least ${MIN_BAIT[t]} tempting dead end${MIN_BAIT[t] === 1 ? '' : 's'}${t > 0 ? ', and the nearest-platform walk must fail' : ''}`).join('\n');

// Optional: the spec a drawn grid came from. stdin is the JSON array of row strings, stdout the spec. Used when a held
// value's own spec is not at hand: each maximal run of '#' is one platform, S and G pick the platform they stand on.
if (process.argv.includes('--decompose')) {
  let raw = '';
  process.stdin.on('data', (d) => { raw += d; });
  process.stdin.on('end', () => {
    let rows;
    try { rows = JSON.parse(raw); } catch (e) { console.error('not a grid'); process.exit(1); }
    if (!Array.isArray(rows)) { console.error('not a grid'); process.exit(1); }
    const platforms = [];
    rows.forEach((row, r) => {
      for (let c = 0; c < row.length; c++) {
        if (row[c] !== '#') continue;
        let e = c;
        while (e + 1 < row.length && row[e + 1] === '#') e++;
        platforms.push([r, c, e]);
        c = e;
      }
    });
    let start = 0, goal = 0;
    rows.forEach((row, r) => {
      for (let c = 0; c < row.length; c++) {
        if (row[c] !== 'S' && row[c] !== 'G') continue;
        const i = platforms.findIndex((p) => p[0] === r + 1 && p[1] <= c && c <= p[2]);
        if (i >= 0) { if (row[c] === 'S') start = i; else goal = i; }
      }
    });
    console.log(JSON.stringify({ platforms, start, goal }));
  });
  return;
}
// Optional: how the check measures difficulty. Used only when the set's briefs are planned
// (not shown to the writer), so the axes that climb are the ones that raise the score.
if (process.argv.includes('--difficulty')) {
  console.log(`Difficulty of a level is one number, built from two parts.
Main part: the hardest jump on the kindest route from start to goal. The player is faster and jumps higher in later seeds, so each of the five tiers of 8 seeds has its own limits (columns and rows a jump can cover), and the tier counts first: every level of a later tier ranks above every level of an earlier one. Inside a tier, a jump costs max(columns crossed / that tier's column limit, rows climbed / that tier's row limit); a drop of any height costs 0. The cost runs from a small fraction (a 1-column hop or 1-row step) to 1.0 (a jump at the tier's limit), in small steps. Wider gaps and taller climbs raise it.
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
The player stands on top of a platform and can drop any height. Platforms are solid: the player cannot jump up through one or drop through one, only around its ends, and a platform over a jump bumps the player's head, so keep the air above a jump open.
The player runs faster and jumps higher in later seeds, so the limits depend on the seed you are writing for. Seeds come in five tiers of 8, and later tiers have smaller platforms:
${tierLines}
So from one platform to the next, for your tier: the next top may be at most that many rows higher, and the distance from the cell you leave to the cell you land on at most that many columns. A route from start to goal must exist through such jumps, and the check plays it with the real jump of your tier.
"start" and "goal" are positions in your platforms list (the first platform is 0): the platform the player starts on and the platform the goal sits on. The program puts S and G on the top of the middle of those platforms, so they always stand on something and you never have to count their rows. A platform on row 0 (the top row) cannot hold either one, so put the goal platform on row 1 or lower. Choose them so the goal platform is high and the way there is long; the start platform does not have to be the lowest.
Count rows from the numbers you write: a platform written at row 13 is 3 rows higher than one written at row 16, and the player stands on the row above the number. Check every step up against your tier's limit before you answer: for seeds 0 to 7 a step from row 16 to row 13 (3 rows) is over the limit of 2, a step from row 16 to row 14 is fine.
A jump needs clear air. Between two platforms the player moves along the standing row of the higher one, then up or down the column at the lower platform's end: every cell of that path must be air. So keep other platforms out of the air directly above the lower platform and out of the gap between the two. Side branches and decoys go beside the route, not across its jumps.
The check measures what makes a level good, so meet it in the first reply:
- at least 8 separate platforms of 3 or more cells each (use 10 to 16: every platform you add must be on the route or part of a reachable dead-end branch of at least 2, so more platforms means more chances to break that rule), and at least 50 solid cells in total;
- no ladders: a platform is a run of at least 3 cells, and a level may hold at most 2 one-cell pillars (a column of single blocks climbs without a real jump);
- platforms are places to stand, not walls of floor: none longer than your tier's maximum above, except on the last 3 rows (rows 17, 18, 19), and the faster you run the smaller the platforms;
- the platforms use the height: their rows span at least 8 rows from the highest to the lowest;
- the goal stands on row 14 or higher (rows 15 to 19 are floor, not where a level ends);
- the shortest route from start to goal takes at least 4 jumps between different platforms (more for later seeds, below), and uses at least 2 different kinds of move (hop, jump or long leap by columns crossed; up, level or down by height), so a shortcut that skips ahead counts against you;
- the way to the goal is unclear, a maze and not a corridor with clutter beside it. Every platform off the shortest route is part of a dead-end branch of at least ${MIN_BRANCH} platforms the player can reach, never a lone block they can ignore. Side branches start at route platforms, so the player stands at a fork and has to choose. At least one dead end looks like the way on (one of its platforms is nearer the goal than a route platform it can be reached from). From seed 8 on, a player who always jumps to the reachable platform nearest the goal, never turning back, must NOT reach it: make the real route go away from the goal for a jump or two (a switchback), with a tempting platform nearer the goal that leads only to a dead end. Floors:
${mazeLines}
- every platform can be reached from the start by jumps inside the limits above, and the shortest route stands on at least 30% of the platforms;
- every jump on the route stays inside the limits above;
- a person has to be able to play it, not only a bot: for seeds 24 and up, the tightest jump on the most forgiving route must leave at least 5 frames (about 80 ms) in which pressing jump still lands it. A jump between platforms that are far apart, with a short platform to take off from, is too tight: give the player a long enough ledge to take off from, or a platform to land on that is wide enough, or bring the two closer than the limit;
- platforms differ in size, for seeds 24 and up: use at least 3 different widths (for example short stepping stones of 3 or 4 cells, ledges of 6 to 8, and one or two long ones up to your tier's maximum), and no single width on more than 60% of the platforms;
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
Reply with the DESIGN line, then one JSON object: {"platforms": [[row, c0, c1], ...], "start": <position of the start platform in your list>, "goal": <position of the goal platform in your list>}`);
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
    if (!Number.isInteger(p) || p < 0 || p >= spec.platforms.length) fail('"' + k + '" must be the position of a platform in your list, a whole number from 0 to ' + (spec.platforms.length - 1) + ' (the first platform is 0), not ' + JSON.stringify(p));
  }
  if (spec.start === spec.goal && spec.platforms.length > 1) fail('"start" and "goal" are the same platform. The goal must be on a different platform from the start.');
  const g = Array.from({ length: H }, () => Array(W).fill('.'));
  for (const p of spec.platforms) {
    if (!Array.isArray(p) || p.length !== 3 || !p.every(Number.isInteger)) fail('platform ' + JSON.stringify(p) + ' is not [row, first_col, last_col]');
    const [r, a, b] = p;
    if (r < 0 || r >= H) fail('platform ' + JSON.stringify(p) + ' has a row outside 0..' + (H - 1));
    for (let c = Math.max(0, Math.min(a, b)); c <= Math.min(W - 1, Math.max(a, b)); c++) g[r][c] = '#';
  }
  for (const [k, ch] of [['start', 'S'], ['goal', 'G']]) {
    const [pr, a, b] = spec.platforms[spec[k]];
    const lo = Math.max(0, Math.min(a, b)), hi = Math.min(W - 1, Math.max(a, b));
    if (pr < 1) fail('"' + k + '" is platform ' + spec[k] + ' at row ' + pr + ', the top row: nothing can stand on it. Use a platform at row 1 or lower.');
    if (lo > hi) fail('platform ' + spec[k] + ' lies outside the level (columns 0..' + (W - 1) + ')');
    const c = Math.floor((lo + hi) / 2);
    g[pr - 1][c] = ch;
  }
  console.log(JSON.stringify(g.map(r => r.join(''))));
});
