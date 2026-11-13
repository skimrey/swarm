#!/usr/bin/env node
/** Ground-truth check for the playable generative platformer. Exit 0 only if every claim holds. */
"use strict";
const fs = require("fs");
const path = require("path");

// WRITER-CONTEXT-BEGIN
// Each value is one level, generateLevel(seed): an array of strings, one per row, written as a literal.
// The order of seeds by difficulty is arranged outside the writer. Write a valid, distinct level; do not try to sort.
const W = 60, H = 20, RISE = 3, REACH = 4;
// WRITER-CONTEXT-END
let failed = false;
function fail(msg) { console.log("CHECK FAILED:", msg); failed = true; }

let levelMod, physMod;
try { levelMod = require("./level.js"); } catch (e) { fail("cannot load level.js: " + e.message); }
try { physMod = require("./physics.js"); } catch (e) { fail("cannot load physics.js: " + e.message); }
if (failed) process.exit(1);

// When compose.json exists the writer gives platform coordinates and a program draws the grid, so repairs that name row strings and single cells are of no use to it.
const COMPOSED = fs.existsSync(path.join(__dirname, "compose.json"));
const generateLevel = levelMod.generateLevel;
const { createState, step, TILE } = physMod;
if (typeof generateLevel !== "function") fail("level.js must export generateLevel");
if (typeof createState !== "function" || typeof step !== "function") fail("physics.js must export createState and step");
if (TILE !== 32) fail("TILE must be 32, got " + TILE);
if (failed) process.exit(1);

// WRITER-CONTEXT-BEGIN
function standing(level, x, y) {
  return y >= 0 && y < level.length - 1 && x >= 0 && x < level[0].length &&
    ".SG".includes(level[y][x]) && level[y + 1][x] === "#";
}
/** Why a cell is not standing, in the level's own numbers. */
function whyNotStanding(level, x, y) {
  if (x < 0 || y < 0) return "it is missing from the level";
  if (y >= level.length || x >= level[y].length) return "that position is outside the level";
  if (level[y][x] === "#") return "that cell is '#'";
  if (y + 1 >= level.length) return "row " + y + " is the last row, so nothing is under it. Add a new row after it that has '#' in column " + x + ", or move the marker up to a cell with '#' under it";
  if (x >= level[y + 1].length) return "row " + (y + 1) + " has length " + level[y + 1].length + ", so it has no cell under column " + x + ". Every row must reach at least as far as the row above it";
  return "the cell under it is '" + level[y + 1][x] + "'";
}
/** A step needs open air all the way: along the higher row between the two columns, then along the column the player rises or drops in. */
function canMove(level, x, y, cx, cy) {
  const air = (xx, yy) => level[yy] !== undefined && xx >= 0 && xx < level[yy].length && level[yy][xx] !== "#";
  const top = Math.min(y, cy);
  for (let xx = Math.min(x, cx); xx <= Math.max(x, cx); xx++) if (!air(xx, top)) return false;
  const col = cy < y ? x : cx;
  for (let yy = top; yy <= Math.max(y, cy); yy++) if (!air(col, yy)) return false;
  return true;
}
function completable(level) {
  const find = (ch) => { for (let y = 0; y < level.length; y++) { const x = level[y].indexOf(ch); if (x >= 0) return [x, y]; } };
  const start = find("S"), goal = find("G");
  if (!start || !goal) return false;
  const cells = [];
  for (let y = 0; y < level.length; y++) for (let x = 0; x < level[0].length; x++) if (standing(level, x, y)) cells.push([x, y]);
  const seen = new Set([start.join(",")]);
  const todo = [start];
  while (todo.length) {
    const [x, y] = todo.pop();
    if (x === goal[0] && y === goal[1]) return true;
    for (const c of cells) {
      const k = c.join(",");
      if (!seen.has(k) && Math.abs(c[0] - x) <= REACH && c[1] >= y - RISE && canMove(level, x, y, c[0], c[1])) { seen.add(k); todo.push(c); }
    }
  }
  return false;
}
/** Design rules: a level that is only a floor, a slab or a corridor is valid and dull. Each issue names what was measured and what to change. */
// compose.js --describe tells the writer these numbers before its first reply; change them together.
const MIN_PLATFORMS = 8, MIN_JUMPS = 4, MIN_SPREAD = 8, MAX_RUN = 30, MIN_KINDS = 2, MIN_OFFROUTE = 3;
function platformRuns(level) {
  const runs = [];
  for (let y = 0; y < level.length; y++) {
    let x = 0;
    while (x < level[y].length) {
      if (level[y][x] !== "#") { x++; continue; }
      let e = x;
      while (e + 1 < level[y].length && level[y][e + 1] === "#") e++;
      let exposed = false;
      for (let c = x; c <= e; c++) if (y === 0 || level[y - 1][c] !== "#") exposed = true;
      runs.push({ row: y, from: x, to: e, exposed });
      x = e + 1;
    }
  }
  return runs;
}
function platformId(level, x, y) {
  let a = x, e = x;
  while (a > 0 && level[y + 1][a - 1] === "#") a--;
  while (e + 1 < level[y + 1].length && level[y + 1][e + 1] === "#") e++;
  return (y + 1) + ":" + a;
}
/** Fewest platform changes from S to G, moving by the same steps as completable(). */
function platformJumps(level) {
  const find = (ch) => { for (let y = 0; y < level.length; y++) { const x = level[y].indexOf(ch); if (x >= 0) return [x, y]; } };
  const start = find("S"), goal = find("G");
  const cells = [];
  for (let y = 0; y < level.length; y++) for (let x = 0; x < level[0].length; x++) if (standing(level, x, y)) cells.push([x, y]);
  const dist = new Map([[start.join(","), 0]]);
  const parent = new Map();
  const dq = [start];
  while (dq.length) {
    const [x, y] = dq.shift();
    const base = dist.get(x + "," + y);
    for (const c of cells) {
      if (Math.abs(c[0] - x) > REACH || c[1] < y - RISE || !canMove(level, x, y, c[0], c[1])) continue;
      const add = platformId(level, x, y) === platformId(level, c[0], c[1]) ? 0 : 1;
      const k = c.join(","), next = base + add;
      if (dist.has(k) && dist.get(k) <= next) continue;
      dist.set(k, next);
      parent.set(k, [x, y]);
      if (add === 0) dq.unshift(c); else dq.push(c);
    }
  }
  const jumps = dist.get(goal.join(","));
  // The shortest route's platform changes, as kinds of jump, and the platforms it stands on.
  const kinds = new Set(), onRoute = new Set();
  if (jumps !== undefined) {
    let at = goal;
    while (at) {
      onRoute.add(platformId(level, at[0], at[1]));
      const from = parent.get(at.join(","));
      if (from && platformId(level, from[0], from[1]) !== platformId(level, at[0], at[1])) {
        const dx = Math.abs(at[0] - from[0]), dy = at[1] - from[1];
        kinds.add((dx <= 1 ? "hop" : dx <= 3 ? "jump" : "long") + "-" + (dy < 0 ? "up" : dy > 0 ? "down" : "level"));
      }
      at = from;
    }
  }
  return { jumps, kinds: [...kinds], onRoute };
}
function designIssues(level) {
  const issues = [];
  const runs = platformRuns(level).filter((r) => r.exposed && r.to - r.from + 1 >= 3);
  if (runs.length < MIN_PLATFORMS) issues.push("has " + runs.length + " platforms (a platform is a run of at least 3 '#' with open air above part of it), fewer than " + MIN_PLATFORMS + ". Add separate platforms at different heights and columns.");
  const slab = runs.find((r) => r.to - r.from + 1 > MAX_RUN && r.row < level.length - 3);
  if (slab) issues.push("has a platform " + (slab.to - slab.from + 1) + " cells long on row " + slab.row + " (columns " + slab.from + " to " + slab.to + "), longer than " + MAX_RUN + ". Only the last 3 rows may hold a run that long; break it into shorter platforms with gaps.");
  if (runs.length) {
    const rows = runs.map((r) => r.row);
    const spread = Math.max(...rows) - Math.min(...rows);
    if (spread < MIN_SPREAD) issues.push("has its platforms within " + spread + " rows of each other, fewer than " + MIN_SPREAD + ". Put platforms at heights that span at least " + MIN_SPREAD + " rows.");
  }
  const route = platformJumps(level);
  const jumps = route.jumps;
  if (jumps !== undefined && route.kinds.length < MIN_KINDS) issues.push("uses " + route.kinds.length + " kind of jump on its shortest route (" + route.kinds.join(", ") + "), fewer than " + MIN_KINDS + ". A level is fun when it asks for different moves: mix short hops, longer leaps, climbs, and drops (kinds are hop/jump/long by columns crossed, and up/level/down).");
  if (jumps !== undefined) {
    const offRoute = runs.filter((r) => { const id = r.row + ":" + r.from; return !route.onRoute.has(id); }).length;
    if (offRoute < MIN_OFFROUTE) issues.push("has " + offRoute + " platforms the shortest route never stands on, fewer than " + MIN_OFFROUTE + ". A level is fun when the player has choices: add side platforms, dead ends, and a second way across so the route is not the only thing there.");
  }
  if (jumps !== undefined && jumps < MIN_JUMPS) issues.push("can be finished in " + jumps + " jumps between platforms by its shortest route, fewer than " + MIN_JUMPS + ". Move S and G further apart, or take out the platforms that let the route skip ahead.");
  return issues;
}
// WRITER-CONTEXT-END
/** Where the route from S stops. Same steps as completable(), reported instead of judged. */
function routeReport(level) {
  const find = (ch) => { for (let y = 0; y < level.length; y++) { const x = level[y].indexOf(ch); if (x >= 0) return [x, y]; } };
  const start = find("S"), goal = find("G");
  const cells = [];
  for (let y = 0; y < level.length; y++) for (let x = 0; x < level[0].length; x++) if (standing(level, x, y)) cells.push([x, y]);
  const seen = new Set([start.join(",")]);
  const todo = [start];
  const reached = [start];
  while (todo.length) {
    const [x, y] = todo.pop();
    for (const c of cells) {
      const k = c.join(",");
      if (!seen.has(k) && Math.abs(c[0] - x) <= REACH && c[1] >= y - RISE && canMove(level, x, y, c[0], c[1])) { seen.add(k); todo.push(c); reached.push(c); }
    }
  }
  const left = (c) => Math.abs(c[0] - goal[0]) + Math.max(0, c[1] - goal[1]);
  let best = reached[0];
  for (const c of reached) if (left(c) < left(best)) best = c;
  let near = null;
  for (const c of cells) {
    if (seen.has(c.join(","))) continue;
    const d = Math.abs(c[0] - best[0]) + Math.abs(c[1] - best[1]);
    if (!near || d < near.d) near = { c, d };
  }
  let gap = "";
  if (near) {
    const dx = Math.abs(near.c[0] - best[0]), up = best[1] - near.c[1];
    const why = [];
    if (dx > REACH) why.push(dx + " columns away, over the limit of " + REACH);
    if (up > RISE) why.push(up + " rows higher, over the limit of " + RISE);
    if (!why.length) why.push("a '#' sits in the open air the jump needs");
    gap = "The nearest standing cell the route cannot reach is at column " + near.c[0] + " row " + near.c[1] + ": " + why.join(" and ") + ". Move a platform closer to column " + best[0] + " row " + best[1] + " or add a stepping platform between.";
  }
  return { standing: cells.length, reached: reached.length, best, left: left(best), goal, gap };
}
/** The first solid cell between the route end and G, named, with the repair that keeps both ends standing. */
function blockedBetween(level, from, to) {
  const top = Math.min(from[1], to[1]);
  const support = (xx, yy) => (xx === from[0] && yy === from[1] + 1) || (xx === to[0] && yy === to[1] + 1);
  const hint = (xx, yy) => support(xx, yy)
    ? " That '#' is the floor the player stands on, so it has to stay. The player gets past a floor by walking sideways along it to where it ends, then dropping or jumping: end that floor row earlier by making cells '.' at a column within 4 of the route end, and keep G on a floor below that gap or on a platform within reach."
    : " Make it '.' unless it is the floor under S or G.";
  for (let xx = Math.min(from[0], to[0]); xx <= Math.max(from[0], to[0]); xx++) if (level[top][xx] === "#") return "Row " + top + " has '#' at column " + xx + ", between column " + from[0] + " and column " + to[0] + "." + hint(xx, top);
  const col = to[1] < from[1] ? from[0] : to[0];
  for (let yy = top; yy <= Math.max(from[1], to[1]); yy++) if (level[yy][col] === "#") return "Column " + col + " has '#' at row " + yy + ", between row " + from[1] + " and row " + to[1] + "." + hint(col, yy);
  return "No single cell blocks it; the platforms between are out of reach of each other.";
}
/** A one-cell change that makes the level completable while S and G keep standing, found by trying them. Nearest to the route end first. */
function oneCellRepair(level, routeEnd) {
  const sy = level.findIndex((r) => r.includes("S")), sx = sy >= 0 ? level[sy].indexOf("S") : -1;
  const gy = level.findIndex((r) => r.includes("G")), gx = gy >= 0 ? level[gy].indexOf("G") : -1;
  const cand = [];
  for (let y = 0; y < level.length; y++) for (let x = 0; x < level[y].length; x++) {
    if (level[y][x] === "#") cand.push([x, y, Math.abs(x - routeEnd[0]) + Math.abs(y - routeEnd[1])]);
  }
  cand.sort((a, b) => a[2] - b[2]);
  for (const [x, y] of cand.slice(0, 400)) {
    const trial = level.slice();
    trial[y] = trial[y].slice(0, x) + "." + trial[y].slice(x + 1);
    if (standing(trial, sx, sy) && standing(trial, gx, gy) && completable(trial)) {
      return " A one-cell change fixes this: row " + y + " (the " + (y + 1) + (y === 0 ? "st" : y === 1 ? "nd" : y === 2 ? "rd" : "th") + " string of the array, counting from 1) is now " + JSON.stringify(level[y]) + " and has to be " + JSON.stringify(trial[y]) + ". Replace that one string and keep every other string exactly as it is.";
    }
  }
  return "";
}
function jumpCost(x, y, nx, ny) {
  const horiz = Math.abs(nx - x) / REACH;
  const up = ny < y ? (y - ny) / RISE : 0;
  return Math.max(horiz, up);
}
function difficulty(level) {
  const find = (ch) => { for (let y = 0; y < level.length; y++) { const x = level[y].indexOf(ch); if (x >= 0) return [x, y]; } };
  const start = find("S"), goal = find("G");
  if (!start || !goal) return [Infinity, Infinity];
  const cells = [];
  for (let y = 0; y < level.length; y++) for (let x = 0; x < level[0].length; x++) if (standing(level, x, y)) cells.push([x, y]);
  const limits = [];
  for (let dx = 0; dx <= REACH; dx++) for (let up = 0; up <= RISE; up++) limits.push(Math.max(dx / REACH, up / RISE));
  limits.sort((a, b) => a - b);
  const thresholds = [...new Set(limits.map((t) => t.toFixed(6)))].map(Number);
  function reaches(limit) {
    const seen = new Set([start.join(",")]);
    const todo = [start];
    while (todo.length) {
      const [x, y] = todo.pop();
      if (x === goal[0] && y === goal[1]) return true;
      for (const c of cells) {
        const k = c.join(",");
        if (seen.has(k) || Math.abs(c[0] - x) > REACH || c[1] < y - RISE || !canMove(level, x, y, c[0], c[1])) continue;
        if (jumpCost(x, y, c[0], c[1]) > limit + 1e-9) continue;
        seen.add(k); todo.push(c);
      }
    }
    return false;
  }
  let bottleneck = null;
  for (const t of thresholds) if (reaches(t)) { bottleneck = t; break; }
  if (bottleneck == null) return [Infinity, Infinity];
  const dist = new Map([[start.join(","), 0]]);
  const dq = [start];
  while (dq.length) {
    const [x, y] = dq.shift();
    const base = dist.get(x + "," + y);
    for (const c of cells) {
      if (Math.abs(c[0] - x) > REACH || c[1] < y - RISE || (c[0] === x && c[1] === y) || !canMove(level, x, y, c[0], c[1])) continue;
      const cost = jumpCost(x, y, c[0], c[1]);
      if (cost > bottleneck + 1e-9) continue;
      const add = cost >= bottleneck - 1e-9 ? 1 : 0;
      const k = c.join(",");
      const next = base + add;
      if (dist.has(k) && dist.get(k) <= next) continue;
      dist.set(k, next);
      if (add === 0) dq.unshift(c); else dq.push(c);
    }
  }
  return [bottleneck, dist.get(goal.join(","))];
}
/** One number per level, for ordering: the hardest jump on the kindest route, plus 0.001 per jump that hard (a jump step is never under 0.083, so a count under 80 never outranks a harder jump). Infinity when the level cannot be finished. */
function difficultyScalar(level) {
  const d = difficulty(level);
  return Number.isFinite(d[0]) ? d[0] + d[1] / 1000 : Infinity;
}
function solidAt(level, px, py) {
  const tx = Math.floor(px / TILE), ty = Math.floor(py / TILE);
  return ty >= 0 && ty < level.length && tx >= 0 && tx < level[0].length && level[ty][tx] === "#";
}
function overlapsSolid(level, s) {
  const pts = [[s.x + 1, s.y + 1], [s.x + s.w - 2, s.y + 1], [s.x + 1, s.y + s.h - 2], [s.x + s.w - 2, s.y + s.h - 2]];
  return pts.some(([px, py]) => solidAt(level, px, py));
}

const levels = [];
const firstSeed = new Map();
for (let seed = 0; seed < 40; seed++) {
  let lv;
  try { lv = generateLevel(seed); } catch (e) { fail("generateLevel(" + seed + ") threw " + e.message); continue; }
  // WRITER-CONTEXT-BEGIN
  // For each seed 0..39, lv = generateLevel(seed) is judged by:
  if (!Array.isArray(lv)) {
    fail("generateLevel(" + seed + ") returned " + typeof lv + ", not an array of strings"); continue;
  }
  if (lv.length < H) {
    fail("generateLevel(" + seed + ") returned " + lv.length + " rows, shorter than " + H); continue;
  }
  const badRow = lv.findIndex((r) => typeof r !== "string");
  if (badRow >= 0) {
    const got = Array.isArray(lv[badRow]) ? "an array of " + lv[badRow].length + " characters" : typeof lv[badRow];
    fail("generateLevel(" + seed + ") row " + badRow + " is " + got + ", not one string. Join each row.");
    continue;
  }
  const short = lv.findIndex((r) => r.length < W);
  if (short >= 0) {
    fail("generateLevel(" + seed + ") row " + short + " has length " + lv[short].length + ", shorter than " + W); continue;
  }
  const bad = [...new Set(lv.join(""))].filter((c) => !"#.SG".includes(c));
  if (bad.length) fail("generateLevel(" + seed + ") uses the characters " + JSON.stringify(bad) + ", which are not allowed. Every cell is one of # . S G; replace each of these with one of them.");
  const flat = lv.join("");
  const ns = flat.split("S").length - 1, ng = flat.split("G").length - 1;
  if (ns < 1 || ng < 1) {
    fail("generateLevel(" + seed + ") has " + ns + " S and " + ng + " G; it needs at least 1 of each. Put the missing one on a '.' cell that has '#' directly under it.");
    continue;
  }
  const airCells = (flat.match(/\./g) || []).length;
  if (airCells < flat.length * 0.15) fail("generateLevel(" + seed + ") has " + airCells + " open '.' cells out of " + flat.length + ", fewer than 15%. A level is mostly platforms with open air between them; replace some '#' cells with '.'.");
  if ((flat.match(/#/g) || []).length < 100) fail("generateLevel(" + seed + ") has " + (flat.match(/#/g) || []).length + " '#' cells, fewer than 100. Add more '#' cells.");
  const sy = lv.findIndex((r) => r.includes("S")), sx = sy >= 0 ? lv[sy].indexOf("S") : -1;
  const gy = lv.findIndex((r) => r.includes("G")), gx = gy >= 0 ? lv[gy].indexOf("G") : -1;
  if (!standing(lv, sx, sy)) fail("generateLevel(" + seed + "): S at column " + sx + " row " + sy + " is not standing on solid: " + whyNotStanding(lv, sx, sy) + ". The cell directly under S must be '#', and S itself must not be '#'.");
  if (!standing(lv, gx, gy)) fail("generateLevel(" + seed + "): G at column " + gx + " row " + gy + " is not standing on solid: " + whyNotStanding(lv, gx, gy) + ". The cell directly under G must be '#', and G itself must not be '#'.");
  if (!completable(lv) && COMPOSED) {
    const r = routeReport(lv);
    fail("generateLevel(" + seed + ") is not completable. A jump rises at most 3 rows and crosses at most 4 empty columns; a drop of any height is allowed; every cell on the way must be open air. " +
      "The route from the start reaches " + r.reached + " of " + r.standing + " standing cells and stops at column " + r.best[0] + " row " + r.best[1] + ". The goal is at column " + r.goal[0] + " row " + r.goal[1] + ". " + (r.gap ? r.gap + " " : "") +
      (r.left > 0 ? "" : "A platform sits between the route end and the goal: " + blockedBetween(lv, r.best, r.goal).split(". ")[0] + ". Move that platform or the goal so the player is not walled in. ") +
      "Change the platform list so every jump from the start to the goal is inside those limits and the goal stands on a platform the route reaches.");
  } else if (!completable(lv)) {
    const r = routeReport(lv);
    fail("generateLevel(" + seed + ") is not completable. From S, each step lands on a cell with '#' directly under it, rising at most 3 rows and moving at most 4 columns. A drop of any distance is allowed. Every cell on the way must be open air (not '#'): the higher of the two rows between the two columns, then the column the player rises or drops in. " +
      "A cell can be stood on when it is '.', 'S' or 'G' and the cell directly below it is '#'; this level has " + r.standing + " such cells and the route from S reaches " + r.reached + " of them. " +
      "The route ends at column " + r.best[0] + " row " + r.best[1] + ", G is at column " + r.goal[0] + " row " + r.goal[1] + ". " + (r.gap ? r.gap + " " : "") +
      (r.left > 0
        ? "Distance left to G (columns plus rows to climb): " + r.left + ", not 0. Change cells so a '#' sits within reach of the route end, on the way to the cell under G."
        : "The route end is level with or above G's column and row, but G is not reached because a cell between them is '#'. " + blockedBetween(lv, r.best, r.goal)) + oneCellRepair(lv, r.best));
  }
  else for (const issue of designIssues(lv)) fail("generateLevel(" + seed + ") " + issue);
  const levelText = lv.join("\n");
  if (firstSeed.has(levelText)) {
    fail("generateLevel(" + seed + ") is the same level as generateLevel(" + firstSeed.get(levelText) + "). Different seeds must return different levels; change the layout, not just one cell.");
  } else {
    firstSeed.set(levelText, seed);
  }
  // WRITER-CONTEXT-END
  levels.push(levelText);
  const score = difficultyScalar(lv);
  if (seed > 0) {
    const prev = difficultyScalar(generateLevel(seed - 1));
    if (score < prev - 1e-9) fail("generateLevel(" + seed + ") is easier than generateLevel(" + (seed - 1) + "): its difficulty is " + score.toFixed(3) + " and the one before it is " + prev.toFixed(3) + ". Seeds must not get easier. Difficulty = the hardest jump on the kindest route from S to G, where a jump costs max(columns crossed / 4, rows climbed / 3) and a drop costs 0, plus 0.001 for each jump on that route that is that hard.");
  }
  if (seed === 39) {
    const first = difficultyScalar(generateLevel(0));
    if (Math.abs(score - first) <= 1e-9) fail("generateLevel(0) and generateLevel(39) have the same difficulty, " + score.toFixed(3) + ". generateLevel(0) must be strictly easier.");
  }
}
if (JSON.stringify(generateLevel(5)) !== JSON.stringify(generateLevel(5))) fail("generateLevel is not deterministic");
if (new Set(levels.slice(0, 20)).size < 15) fail("generation is not varied across seeds: the first 20 seeds give " + new Set(levels.slice(0, 20)).size + " different levels, fewer than 15");
if (failed) process.exit(1);
const small = generateLevel(3, 40, 15);
if (!Array.isArray(small) || small.length < 15 || small.some((r) => typeof r !== "string" || r.length < 40)) fail("generateLevel(3, 40, 15) must return at least 15 rows of at least 40");

function sim(level, frames, inputFn) {
  let s = createState(level);
  const trace = [Object.assign({}, s)];
  for (let i = 0; i < frames; i++) {
    s = step(s, inputFn(i, s)) || s;
    if (!s || typeof s.x !== "number" || typeof s.y !== "number" || !isFinite(s.x) || !isFinite(s.y)) {
      fail("step returned a non-numeric position at frame " + i); return null;
    }
    trace.push(Object.assign({}, s));
  }
  return trace;
}

const level = generateLevel(1);
const spawned = createState(level);
if (spawned.w < 8 || spawned.h < 8 || spawned.w > TILE - 2) fail("player size must fit in a tile (w=" + spawned.w + " h=" + spawned.h + ")");
const sy = level.findIndex((r) => r.includes("S")), sx = level[sy].indexOf("S");
if (Math.abs(spawned.x - sx * TILE) > TILE || Math.abs(spawned.y - sy * TILE) > TILE) fail("player does not spawn on the S tile");
if (spawned.won) fail("player must not start already won");

const idle = sim(level, 90, () => ({ left: false, right: false, jump: false }));
if (idle) {
  if (idle.some((s) => overlapsSolid(level, s))) fail("player falls into solid ground when standing still");
  const drop = Math.max(...idle.map((s) => s.y)) - idle[0].y;
  if (drop > TILE) fail("player fell " + drop.toFixed(0) + "px through the floor while standing still");
}
const hop = sim(level, 40, (i) => ({ left: false, right: false, jump: i < 8 }));
if (hop) {
  const rose = hop[0].y - Math.min(...hop.map((s) => s.y));
  if (rose < 16) fail("holding jump did not lift the player (rose " + rose.toFixed(0) + "px)");
}

const tiny = [
  "................",
  "..S........G....",
  "################",
].map((r) => r); // width 16, height 3
const run = sim(tiny, 360, () => ({ left: false, right: true, jump: false }));
if (run && !run.some((s) => s.won)) fail("holding right on a flat level never sets won, so the goal is not detected");
if (run && run.some((s) => overlapsSolid(tiny, s))) fail("player overlaps solid tiles while walking");

const htmlPath = path.join(__dirname, "index.html");
if (!fs.existsSync(htmlPath)) fail("index.html is missing");
else {
  const html = fs.readFileSync(htmlPath, "utf8");
  for (const need of ["<canvas", "keydown", "keyup", "requestAnimationFrame", "level.js", "physics.js"]) {
    if (!html.includes(need)) fail("index.html does not contain " + JSON.stringify(need));
  }
  if (!/ArrowLeft|KeyA/.test(html) || !/ArrowRight|KeyD/.test(html) || !/ArrowUp|Space|KeyW/.test(html)) {
    fail("index.html does not handle left, right and jump keys");
  }
  const startSeed = (html.match(/let\s+currentSeed\s*=\s*(\d+)/) || [])[1];
  if (startSeed === undefined) fail("index.html must start with 'let currentSeed = N;' so the first level is known.");
  else if (!Array.isArray(generateLevel(Number(startSeed))) || generateLevel(Number(startSeed)).length < H) fail("index.html starts at seed " + startSeed + ", but generateLevel(" + startSeed + ") has " + ((generateLevel(Number(startSeed)) || []).length) + " rows, fewer than " + H + ". Start at a seed from 0 to 39 so the first screen shows a level.");
  if (!/wrapSeed|%\s*40|currentSeed\s*=\s*0/.test(html)) fail("index.html must go back to seed 0 after the last level, or Next Seed shows an empty level.");
  if (html.includes("Math.random")) fail("index.html picks a random seed. N and Next Seed must load the next seed, currentSeed + 1.");
  const advances = html.match(/currentSeed\s*(?:\+\+|\+=\s*1|\+\s*1)/g) || [];
  if (advances.length < 2) fail("index.html must advance the seed in both the N key and the Next Seed button, as currentSeed + 1.");
}

if (failed) process.exit(1);
console.log("CHECK PASSED: 40 completable levels, physics (stand, jump, walk, goal), and a playable page");
