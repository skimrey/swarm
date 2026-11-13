const TILE = 32;

/**
 * Creates the initial state for the game.
 * @param {string[]} level - The level map.
 * @returns {object} The initial game state.
 */
function createState(level) {
  let startX = 0;
  let startY = 0;
  let found = false;

  for (let r = 0; r < level.length; r++) {
    for (let c = 0; c < level[r].length; c++) {
      if (level[r][c] === 'S') {
        startX = c * TILE;
        startY = r * TILE;
        found = true;
        break;
      }
    }
    if (found) break;
  }

  return {
    x: startX,
    y: startY,
    vx: 0,
    vy: 0,
    w: 20,
    h: 20,
    won: false,
    level: level
  };
}

/**
 * Applies one frame of physics to the game state.
 * @param {object} state - The current game state.
 * @param {object} input - The user input {left, right, jump}.
 * @returns {object} The new game state.
 */
function step(state, input) {
  const gravity = 0.5;
  const moveSpeed = 4;
  const jumpStrength = -10; // peak rise ~100px = 3.1 tiles, so a 3-row step is possible (check_game.js RISE = 3)
  const level = state.level;

  let newState = { ...state };

  const isColliding = (px, py, pw, ph) => {
    for (let r = 0; r < level.length; r++) {
      for (let c = 0; c < level[r].length; c++) {
        if (level[r][c] === '#') {
          const tx = c * TILE;
          const ty = r * TILE;
          if (px < tx + TILE && px + pw > tx && py < ty + TILE && py + ph > ty) {
            return true;
          }
        }
      }
    }
    return false;
  };

  // Horizontal movement
  if (input.left) {
    newState.vx = -moveSpeed;
  } else if (input.right) {
    newState.vx = moveSpeed;
  } else {
    newState.vx = 0;
  }

  // Vertical movement (Gravity)
  newState.vy += gravity;

  // Grounded check for jumping
  const checkGrounded = (px, py, pw, ph) => {
    const feetY = py + ph + 0.1;
    for (let r = 0; r < level.length; r++) {
      for (let c = 0; c < level[r].length; c++) {
        if (level[r][c] === '#') {
          const tx = c * TILE;
          const ty = r * TILE;
          if (px < tx + TILE && px + pw > tx && feetY >= ty && feetY <= ty + TILE) {
            return true;
          }
        }
      }
    }
    return false;
  };

  const grounded = checkGrounded(state.x, state.y, state.w, state.h);

  if (input.jump && grounded) {
    newState.vy = jumpStrength;
  }

  // X-axis resolution
  let nextX = state.x + newState.vx;
  if (!isColliding(nextX, state.y, newState.w, newState.h)) {
    newState.x = nextX;
  } else {
    newState.vx = 0;
  }

  // Y-axis resolution
  let nextY = state.y + newState.vy;
  if (!isColliding(newState.x, nextY, newState.w, newState.h)) {
    newState.y = nextY;
  } else {
    if (newState.vy > 0) {
      // Falling: snap to top of tile
      newState.y = Math.floor((nextY + newState.h) / TILE) * TILE - newState.h;
    } else {
      // Jumping up: snap to bottom of tile
      newState.y = Math.ceil(nextY / TILE) * TILE;
    }
    newState.vy = 0;
  }

  // Goal detection
  for (let r = 0; r < level.length; r++) {
    for (let c = 0; c < level[r].length; c++) {
      if (level[r][c] === 'G') {
        const gx = c * TILE;
        const gy = r * TILE;
        if (
          newState.x < gx + TILE &&
          newState.x + newState.w > gx &&
          newState.y < gy + TILE &&
          newState.y + newState.h > gy
        ) {
          newState.won = true;
        }
      }
    }
  }

  return newState;
}

module.exports = {
  TILE,
  createState,
  step
};
