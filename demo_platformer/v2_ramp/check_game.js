#!/usr/bin/env node
/** Ground-truth check for the playable generative platformer. Exit 0 only if every claim holds. */
"use strict";
const fs = require("fs");
const path = require("path");

// WRITER-CONTEXT-BEGIN
// Each value is one level, generateLevel(seed): an array of strings, one per row, written as a literal.
// The order of seeds by difficulty is arranged outside the writer. Write a valid, distinct level; do not try to sort.
const W = 60, H = 20, RISE = 3, REACH = 4;
// A level must be finishable with the game's real jump, not only on paper: the player's jump peaks about 3.1 rows
// above the take-off row, so a 3-row step up works only when the cells it passes through are open air; a '#' that
// overhangs the jump bumps the player's head and stops the climb. The check plays the real physics to find out.
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
const MIN_PLATFORMS = 8, MIN_SPREAD = 8, MAX_RUN = 30, MIN_KINDS = 2, MIN_OFFROUTE = 3;
// A level is a journey, not a fall. The goal must be an arrival: it stands on row MAX_GOAL_ROW or higher,
// so the bottom five rows (15 to 19) are floor, not where the level ends.
// Every platform can be reached from the start (scenery nobody can use is clutter, not a shape),
// and the shortest route stands on at least MIN_ROUTE_SHARE of the platforms (a shape the goal never asks
// the player to touch is wasted).
const MAX_GOAL_ROW = 14, MAX_UNREACHABLE = 0, MIN_ROUTE_SHARE = 0.4;
// The ramp: later seeds must ask for more. Seeds come in bands of 8 (band 0 = seeds 0-7 ... band 4 = seeds 32-39).
// Each row is the floor for the bands in order. compose.js --describe prints the same table; change them together.
//   MIN_CLIMB: rows the player must climb (least climbing over all routes; drops count nothing)
//   MIN_BACK: jumps the player must make in the LESS used direction, so the route doubles back
//   MIN_FALL: the worst fall on the shortest route costs this many jumps to climb back from
//   MIN_JUMPS: jumps between platforms on the shortest route
const BAND = 8;
const MIN_CLIMB = [2, 5, 8, 11, 14], MIN_BACK = [0, 1, 1, 2, 2], MIN_FALL = [1, 2, 3, 4, 5], MIN_JUMPS = [4, 5, 6, 7, 8];
const band = (seed) => Math.min(MIN_CLIMB.length - 1, Math.max(0, Math.floor(seed / BAND)));
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
function designIssues(level, seed) {
  const issues = [];
  const b = band(seed);
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
  const gy = level.findIndex((r) => r.includes("G"));
  if (gy > MAX_GOAL_ROW) issues.push("has its goal on row " + gy + ", lower than row " + MAX_GOAL_ROW + ". The goal is where the journey ends, not the bottom of a fall: rows " + (MAX_GOAL_ROW + 1) + " to " + (level.length - 1) + " are floor. Put the goal on a platform at row " + MAX_GOAL_ROW + " or higher and make the route climb to it.");
  if (jumps !== undefined) {
    const m = challenge(level);
    if (m.unreachable.length > MAX_UNREACHABLE) issues.push("has " + m.unreachable.length + " platforms the player can never reach from S (" + m.unreachable.slice(0, 4).map((r) => "row " + r.row + " columns " + r.from + " to " + r.to).join("; ") + "), more than " + MAX_UNREACHABLE + ". A shape nobody can use is clutter. Move each one to within 4 columns and 3 rows of a platform the player can reach, or take it out.");
    const onShare = m.onRoute / Math.max(1, runs.length);
    if (onShare < MIN_ROUTE_SHARE - 1e-9) issues.push("has a shortest route that stands on " + m.onRoute + " of its " + runs.length + " platforms, " + Math.round(onShare * 100) + "%, under " + Math.round(MIN_ROUTE_SHARE * 100) + "%. The shapes the level builds should be the way to the goal, not decoration beside it. Make the route run through more of them, or take out the platforms the route never needs (but keep at least " + MIN_OFFROUTE + " off the route as choices).");
    if (jumps < MIN_JUMPS[b]) issues.push("can be finished in " + jumps + " jumps between platforms by its shortest route, fewer than " + MIN_JUMPS[b] + " (the floor for seeds " + b * BAND + " to " + (b * BAND + BAND - 1) + "). Move S and G further apart, or take out the platforms that let the route skip ahead.");
    if (m.climb < MIN_CLIMB[b]) issues.push("makes the player climb " + m.climb + " rows on its easiest route, fewer than " + MIN_CLIMB[b] + " (the floor for seeds " + b * BAND + " to " + (b * BAND + BAND - 1) + "). A drop costs nothing, so a level that falls from S to G climbs nothing. Put G higher than the platforms before it, or add rising platforms (each step up of up to 3 rows counts its rows) that every route has to go through.");
    if (m.back < MIN_BACK[b]) issues.push("makes the player jump back against the main direction " + m.back + " times on its easiest route, fewer than " + MIN_BACK[b] + " (the floor for seeds " + b * BAND + " to " + (b * BAND + BAND - 1) + "): its route goes " + (m.left <= m.right ? "left only " + m.left + " times and right " + m.right : "right only " + m.right + " times and left " + m.left) + " times. Make the route double back, as a zigzag or switchback: after a run one way, a platform that can only be reached by jumping the other way, and then on again.");
    if (m.fall < MIN_FALL[b]) issues.push("has a worst fall of " + m.fall + " jumps to climb back (the fall is a drop off a platform on the shortest route, the cost is the fewest jumps to get back onto the platform it was dropped from), fewer than " + MIN_FALL[b] + " (the floor for seeds " + b * BAND + " to " + (b * BAND + BAND - 1) + "). A missed jump should cost the player something. Make a high platform on the route drop onto a low one that needs several jumps to climb back, as the base of a tower or staircase does.");
  }
  return issues;
}
// WRITER-CONTEXT-END
/** Every standing cell of a level and every step between them (the same steps as completable()), built once per level. */
const graphs = new Map();
function levelGraph(level) {
  const key = level.join("\n");
  if (graphs.has(key)) return graphs.get(key);
  const cells = [];
  for (let y = 0; y < level.length; y++) for (let x = 0; x < level[0].length; x++) if (standing(level, x, y)) cells.push({ x, y, p: platformId(level, x, y) });
  const adj = cells.map(() => []);
  for (let i = 0; i < cells.length; i++) for (let j = 0; j < cells.length; j++) {
    const a = cells[i], b = cells[j];
    if (i !== j && Math.abs(b.x - a.x) <= REACH && b.y >= a.y - RISE && canMove(level, a.x, a.y, b.x, b.y)) adj[i].push(j);
  }
  const find = (ch) => { for (let y = 0; y < level.length; y++) { const x = level[y].indexOf(ch); if (x >= 0) return [x, y]; } };
  const s = find("S"), g = find("G");
  const graph = { cells, adj, start: cells.findIndex((c) => s && c.x === s[0] && c.y === s[1]), goal: cells.findIndex((c) => g && c.x === g[0] && c.y === g[1]) };
  graphs.set(key, graph);
  return graph;
}
/** Least total cost from one cell to every other, where a step costs cost(from, to) >= 0. */
function leastCost(graph, from, cost) {
  const n = graph.cells.length, d = new Array(n).fill(Infinity), done = new Array(n).fill(false), parent = new Array(n).fill(-1);
  d[from] = 0;
  for (;;) {
    let u = -1;
    for (let i = 0; i < n; i++) if (!done[i] && d[i] < Infinity && (u < 0 || d[i] < d[u])) u = i;
    if (u < 0) break;
    done[u] = true;
    for (const v of graph.adj[u]) {
      const c = d[u] + cost(graph.cells[u], graph.cells[v]);
      if (c < d[v]) { d[v] = c; parent[v] = u; }
    }
  }
  return { d, parent };
}
/** What the level asks of the player beyond the hardest jump. Each number is the least over ALL routes (so no route avoids it), except fall, which is read off the shortest route.
 *  climb: rows the player must go up. back: jumps in the less used direction. fall: the worst drop on the shortest route, in jumps to get back.
 *  unreachable: platforms the player can never stand on. onRoute: platforms the shortest route stands on. */
const challenges = new Map();
function challenge(level) {
  const key = level.join("\n");
  if (challenges.has(key)) return challenges.get(key);
  const g = levelGraph(level);
  const out = { climb: 0, back: 0, left: 0, right: 0, fall: 0, jumps: 0, onRoute: 0, unreachable: [] };
  if (g.start < 0 || g.goal < 0) { challenges.set(key, out); return out; }
  const jump = (a, b) => (a.p !== b.p ? 1 : 0);
  out.climb = leastCost(g, g.start, (a, b) => Math.max(0, a.y - b.y)).d[g.goal];
  out.left = leastCost(g, g.start, (a, b) => (a.p !== b.p && b.x < a.x ? 1 : 0)).d[g.goal];
  out.right = leastCost(g, g.start, (a, b) => (a.p !== b.p && b.x > a.x ? 1 : 0)).d[g.goal];
  out.back = Math.min(out.left, out.right);
  const shortest = leastCost(g, g.start, jump);
  out.jumps = shortest.d[g.goal];
  // The shortest route, then a fall off each of its cells: the cost is the fewest jumps back onto the cell it fell from.
  const route = [];
  for (let at = g.goal; at >= 0; at = shortest.parent[at]) route.push(at);
  const recover = new Map();
  const from = (k) => { if (!recover.has(k)) recover.set(k, leastCost(g, k, jump).d); return recover.get(k); };
  for (const c of route) for (const v of g.adj[c]) {
    const a = g.cells[c], b = g.cells[v];
    if (b.y > a.y && b.p !== a.p) { const back = from(v)[c]; if (back < Infinity && back > out.fall) out.fall = back; }
  }
  const onRoute = new Set(route.map((c) => g.cells[c].p));
  const reach = leastCost(g, g.start, () => 0).d;
  const reached = new Set(g.cells.filter((c, i) => reach[i] < Infinity).map((c) => c.p));
  for (const r of platformRuns(level).filter((r) => r.exposed && r.to - r.from + 1 >= 3)) {
    const id = r.row + ":" + r.from;
    if (onRoute.has(id)) out.onRoute++;
    if (!reached.has(id)) out.unreachable.push(r);
  }
  challenges.set(key, out);
  return out;
}
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
/** One number per level, for ordering. The hardest jump on the kindest route is the main part. Within the same hardest jump, a level that asks more of the player
 *  (more climbing, more doubling back, a costlier fall, more jumps) is harder: up to 0.04 for that "depth", plus 0.001 per jump that is as hard as the hardest (at most 30 count).
 *  The extras together stay under one jump step (0.083), so they only order levels that share a hardest jump. Infinity when the level cannot be finished. */
const DEPTH_FULL = { climb: 16, back: 3, fall: 6, jumps: 8 };
function depth(level) {
  const m = challenge(level);
  const part = (v, full) => Math.min(1, Math.max(0, v) / full);
  return (part(m.climb, DEPTH_FULL.climb) + part(m.back, DEPTH_FULL.back) + part(m.fall, DEPTH_FULL.fall) + part(m.jumps - 4, DEPTH_FULL.jumps)) / 4;
}
function difficultyScalar(level) {
  const d = difficulty(level);
  return Number.isFinite(d[0]) ? d[0] + 0.04 * depth(level) + 0.001 * Math.min(d[1], 30) : Infinity;
}
function solidAt(level, px, py) {
  const tx = Math.floor(px / TILE), ty = Math.floor(py / TILE);
  return ty >= 0 && ty < level.length && tx >= 0 && tx < level[0].length && level[ty][tx] === "#";
}
function overlapsSolid(level, s) {
  const pts = [[s.x + 1, s.y + 1], [s.x + s.w - 2, s.y + 1], [s.x + 1, s.y + s.h - 2], [s.x + s.w - 2, s.y + s.h - 2]];
  return pts.some(([px, py]) => solidAt(level, px, py));
}

if (process.env.MEASURE) {
  // Diagnostic, not a check: prints what each level asks, so the floors above can be set against real levels. MEASURE=1 node check_game.js
  const rows = [];
  for (let seed = 0; seed < 40; seed++) {
    const lv = generateLevel(seed);
    if (!Array.isArray(lv) || !lv.length) continue;
    const m = challenge(lv), runs = platformRuns(lv).filter((r) => r.exposed && r.to - r.from + 1 >= 3);
    rows.push({ seed, band: band(seed), goalRow: lv.findIndex((r) => r.includes("G")), jumps: m.jumps, climb: m.climb, back: m.back, fall: m.fall, platforms: runs.length, onRoute: m.onRoute, unreachable: m.unreachable.length, difficulty: difficultyScalar(lv).toFixed(3), issues: designIssues(lv, seed).length });
  }
  console.table(rows);
  process.exit(0);
}

/** Plays the game's real physics: breadth-first over (x, y, vertical speed) with every input, to see whether S can reach G. */
const PHYS = { gravity: 0.5, speed: 4, jump: -10 };
function physicsModel(level) {
  const rows = level.length, cols = level[0].length;
  const solid = new Uint8Array(rows * cols);
  for (let r = 0; r < rows; r++) for (let c = 0; c < level[r].length && c < cols; c++) if (level[r][c] === "#") solid[r * cols + c] = 1;
  const hit = (px, py, w, h) => {
    const c0 = Math.max(0, Math.floor(px / TILE)), c1 = Math.min(cols - 1, Math.floor((px + w - 1e-9) / TILE));
    const r0 = Math.max(0, Math.floor(py / TILE)), r1 = Math.min(rows - 1, Math.floor((py + h - 1e-9) / TILE));
    for (let r = r0; r <= r1; r++) for (let c = c0; c <= c1; c++) if (solid[r * cols + c]) return true;
    return false;
  };
  const grounded = (px, py, w, h) => {
    const fy = py + h + 0.1;
    const c0 = Math.max(0, Math.floor(px / TILE)), c1 = Math.min(cols - 1, Math.floor((px + w - 1e-9) / TILE));
    for (let r = 0; r < rows; r++) { const ty = r * TILE; if (fy >= ty && fy <= ty + TILE) for (let c = c0; c <= c1; c++) if (solid[r * cols + c]) return true; }
    return false;
  };
  const next = (s, inp, w, h) => {
    const vx = inp.left ? -PHYS.speed : inp.right ? PHYS.speed : 0;
    let vy = s.vy + PHYS.gravity;
    if (inp.jump && grounded(s.x, s.y, w, h)) vy = PHYS.jump;
    let x = s.x; if (!hit(s.x + vx, s.y, w, h)) x = s.x + vx;
    let y = s.y; const ny = s.y + vy;
    if (!hit(x, ny, w, h)) y = ny;
    else { y = vy > 0 ? Math.floor((ny + h) / TILE) * TILE - h : Math.ceil(ny / TILE) * TILE; vy = 0; }
    return { x, y, vy };
  };
  return { next, rows };
}
const PHYS_INPUTS = [];
for (const d of [-1, 0, 1]) for (const j of [false, true]) PHYS_INPUTS.push({ left: d < 0, right: d > 0, jump: j });
/** { ok } or { ok: false, col, row }: the standing cell nearest G that real play reaches. */
function physicsPlayable(level) {
  const st = createState(level), w = st.w, h = st.h;
  const gy = level.findIndex((r) => r.includes("G")), gx = gy >= 0 ? level[gy].indexOf("G") : -1;
  const M = physicsModel(level);
  const key = (s) => s.x * 1e6 + s.y * 1e2 + Math.round(s.vy * 2);
  const seen = new Set(), todo = [{ x: st.x, y: st.y, vy: 0 }];
  seen.add(key(todo[0]));
  let best = null, bestLeft = Infinity;
  while (todo.length) {
    const s = todo.pop();
    if (s.x < gx * TILE + TILE && s.x + w > gx * TILE && s.y < gy * TILE + TILE && s.y + h > gy * TILE) return { ok: true };
    if (s.vy === 0) {
      const col = Math.floor((s.x + w / 2) / TILE), row = Math.floor((s.y + h - 1) / TILE);
      const left = Math.abs(col - gx) + Math.max(0, row - gy);
      if (left < bestLeft) { bestLeft = left; best = { col, row }; }
    }
    for (const inp of PHYS_INPUTS) {
      const t = M.next(s, inp, w, h);
      if (t.y > M.rows * TILE + 200) continue;
      const k = key(t); if (!seen.has(k)) { seen.add(k); todo.push(t); }
    }
  }
  return { ok: false, col: best ? best.col : -1, row: best ? best.row : -1 };
}
/** The model above must agree with the real step() frame for frame, or the check would judge a game that is not the one played. */
function physicsModelDrift(level) {
  const st0 = createState(level), M = physicsModel(level);
  let a = st0, b = { x: st0.x, y: st0.y, vy: 0 }, rs = 12345;
  const rnd = () => (rs = (rs * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
  for (let i = 0; i < 2000; i++) {
    const inp = { left: rnd() < 0.3, right: rnd() < 0.5, jump: rnd() < 0.2 };
    a = step(a, inp); b = M.next(b, inp, st0.w, st0.h);
    if (Math.abs(a.x - b.x) > 1e-9 || Math.abs(a.y - b.y) > 1e-9) return "frame " + i + ": step() gives (" + a.x + ", " + a.y + ") and the check's model gives (" + b.x + ", " + b.y + ")";
  }
  return null;
}
{
  const drift = physicsModelDrift(generateLevel(1));
  if (drift) fail("physics.js no longer matches the check's model of it (" + drift + "). Gravity 0.5, move speed 4, jump -10, 20x20 body, tiles 32: restore physics.js to those, or update PHYS in check_game.js.");
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
  else {
    for (const issue of designIssues(lv, seed)) fail("generateLevel(" + seed + ") " + issue);
    const play = physicsPlayable(lv);
    if (!play.ok) {
      const gyy = lv.findIndex((r) => r.includes("G")), gxx = gyy >= 0 ? lv[gyy].indexOf("G") : -1;
      fail("generateLevel(" + seed + ") cannot be finished with the game's real jump. Playing it, the player gets as near the goal as column " + play.col + " row " + play.row + " (the goal is at column " + gxx + " row " + gyy + ") and no further. The jump rises about 3.1 rows, and the higher the step the shorter the gap: a step of 3 rows crosses at most 3 empty columns, a step of 2 rows at most 4. A '#' that sits over the take-off or over the path of the jump bumps the player's head and stops the climb. Move or shorten the platform in the way, lower the step, or narrow the gap.");
    }
  }
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
    if (score < prev - 1e-9) fail("generateLevel(" + seed + ") is easier than generateLevel(" + (seed - 1) + "): its difficulty is " + score.toFixed(3) + " and the one before it is " + prev.toFixed(3) + ". Seeds must not get easier. Difficulty = the hardest jump on the kindest route from S to G, where a jump costs max(columns crossed / 4, rows climbed / 3) and a drop costs 0; then, among levels with the same hardest jump, the one that asks more is harder: rows it forces the player to climb (full at 16), jumps back against the main direction (full at 3), the cost of its worst fall in jumps (full at 6) and jumps on its shortest route above 4 (full at 12), worth up to 0.04 together; then 0.001 for each jump on the route that is as hard as the hardest.");
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
