function mulberry32(a) {
  return function() {
    let t = a += 0x6D2B79F5;
    t = Math.imul(t ^ t >>> 15, t | 1);
    t ^= t + Math.imul(t ^ t >>> 7, t | 61);
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}

/**
 * Generates a level grid.
 * Rules:
 * - Exactly one 'S' and one 'G'.
 * - 'S' and 'G' must be on '#' tiles (meaning the tile below them is '#').
 * - Guaranteed path from S to G where no jump exceeds 3 tiles up or 4 tiles across.
 * - 'S' and 'G' themselves are not '#'.
 * 
 * @param {number} seed 
 * @param {number} [width=60] 
 * @param {number} [height=20] 
 * @returns {string[]}
 */
function generateLevel(seed, width = 60, height = 20) {
  const prng = mulberry32(seed);

  const isValid = (x, y) => x >= 0 && x < width && y >= 0 && y < height;

  while (true) {
    // 1. Initialize grid with '.'
    let grid = Array.from({ length: height }, () => Array(width).fill('.'));

    // 2. Generate random noise '#'
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        if (prng() < 0.2) {
          grid[y][x] = '#';
        }
      }
    }

    // 3. Generate a path of "standing points"
    // A standing point (x, y) implies the player is at (x, y) and grid[y+1][x] is '#'
    let standingPoints = [];
    let curX = Math.floor(prng() * width);
    let curY = Math.floor(prng() * (height - 1));

    standingPoints.push({ x: curX, y: curY });

    // Attempt to build a path of up to 50 steps
    for (let i = 0; i < 50; i++) {
      let possibleMoves = [];
      // Jump rules: 
      // - Max 3 tiles up: (curY - nextY) <= 3
      // - Max 4 tiles across: abs(curX - nextX) <= 4
      // - Any drop is allowed: nextY > curY
      for (let dx = -4; dx <= 4; dx++) {
        for (let dy = -3; dy < height; dy++) {
          if (dx === 0 && dy === 0) continue;
          let nx = curX + dx;
          let ny = curY + dy;

          if (isValid(nx, ny) && ny < height - 1) {
            // Check jump constraints
            const isRisingTooHigh = (curY - ny) > 3;
            const isTooWide = Math.abs(dx) > 4;

            if (!isRisingTooHigh && !isTooWide) {
              possibleMoves.push({ x: nx, y: ny });
            }
          }
        }
      }

      if (possibleMoves.length === 0) break;
      let move = possibleMoves[Math.floor(prng() * possibleMoves.length)];
      curX = move.x;
      curY = move.y;
      standingPoints.push({ x: curX, y: curY });
    }

    // We need at least two distinct points for S and G
    if (standingPoints.length < 2) continue;

    // 4. Apply standing points to grid
    // For every point in the path, ensure the tile below is '#'
    for (let p of standingPoints) {
      grid[p.y + 1][p.x] = '#';
    }

    // 5. Pick S and G
    // S is the first point, G is the last point
    let S = standingPoints[0];
    let G = standingPoints[standingPoints.length - 1];

    // Ensure S and G are not the same tile
    if (S.x === G.x && S.y === G.y) continue;

    // Ensure S and G are not '#' (they are standing on '#')
    // We must overwrite any '#' that might have been placed at S or G's position
    grid[S.y][S.x] = 'S';
    grid[G.y][G.x] = 'G';

    // Final check: S and G must not be '#'
    // (The loop above ensures grid[S.y][S.x] is 'S', not '#')
    
    // 6. Return as array of strings
    return grid.map(row => row.join(''));
  }
}

module.exports = { generateLevel };
