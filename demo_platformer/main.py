# --- .env loader (added by codegen.py) ---
import os as _os
if _os.path.isfile('/Users/skimrey/Documents/Local LLMs/begin/.env'):
    with open('/Users/skimrey/Documents/Local LLMs/begin/.env', "r", encoding="utf-8") as _envf:
        for _line in _envf:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _, _v = _line.partition("=")
            _os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
del _os
# --- .env loader (added by codegen.py) ---

import os
import json
import subprocess
import sys

def write_index_html():
    """
    Writes the index.html file required for the playable platformer.
    """
    html_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Generative Platformer</title>
    <style>
        body, html { margin: 0; padding: 0; overflow: hidden; width: 100%; height: 100%; background: #0a0a12; }
        canvas { display: block; width: 100vw; height: 100vh; }
        #ui { position: absolute; top: 20px; left: 20px; color: #ffffff; font-family: 'Courier New', Courier, monospace; pointer-events: none; text-shadow: 2px 2px 4px rgba(0,0,0,0.8); }
        #msg { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); color: #ffffff; background: rgba(233, 69, 96, 0.9); padding: 30px 50px; border-radius: 15px; font-size: 48px; font-family: sans-serif; display: none; text-align: center; box-shadow: 0 0 30px rgba(0,0,0,0.5); }
        .hint { font-size: 18px; color: #4ecca3; margin-top: 5px; }
        .controls { font-size: 14px; color: #aaa; margin-top: 10px; }
    </style>
</head>
<body>
    <div id="ui">
        <div id="seed-display" style="font-size: 24px; font-weight: bold;"></div>
        <div class="hint">Reach the goal!</div>
        <div class="controls">[N] Next Seed | [R] Restart</div>
    </div>
    <div id="msg">YOU WIN!<div style="font-size: 20px; color: white; margin-top: 10px;">Press N for next, R to restart</div></div>
    <canvas id="gameCanvas"></canvas>

    <script src="level.js"></script>
    <script src="physics.js"></script>
    <script>
        const canvas = document.getElementById('gameCanvas');
        const ctx = canvas.getContext('2d');
        const msg = document.getElementById('msg');
        const seedDisplay = document.getElementById('seed-display');

        let currentSeed = 12345;
        let levelGrid = [];
        let state = null;
        let keys = { left: false, right: false, jump: false };

        function resize() {
            canvas.width = window.innerWidth;
            canvas.height = window.innerHeight;
        }
        window.addEventListener('resize', resize);
        resize();

        window.addEventListener('keydown', e => {
            if (e.code === 'ArrowLeft' || e.code === 'KeyA') keys.left = true;
            if (e.code === 'ArrowRight' || e.code === 'KeyD') keys.right = true;
            if (e.code === 'Space' || e.code === 'ArrowUp' || e.code === 'KeyW') keys.jump = true;
            if (e.code === 'KeyN') nextLevel();
            if (e.code === 'KeyR') initGame(currentSeed);
        });

        window.addEventListener('keyup', e => {
            if (e.code === 'ArrowLeft' || e.code === 'KeyA') keys.left = false;
            if (e.code === 'ArrowRight' || e.code === 'KeyD') keys.right = false;
            if (e.code === 'Space' || e.code === 'ArrowUp' || e.code === 'KeyW') keys.jump = false;
        });

        function initGame(seed) {
            currentSeed = seed;
            levelGrid = generateLevel(seed).map(row => row.split(''));
            state = createState(levelGrid);
            msg.style.display = 'none';
            seedDisplay.innerText = 'Seed: ' + seed;
        }

        function nextLevel() {
            initGame(Math.floor(Math.random() * 1000000));
        }

        function update() {
            if (!state || state.won) return;
            
            state = step(state, keys);

            if (state.y > levelGrid.length * TILE) {
                initGame(currentSeed);
            }

            if (state.won) {
                msg.style.display = 'block';
            }
        }

        function draw() {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            if (!state) return;

            const camX = state.x + state.w/2 - canvas.width / 2;
            const camY = state.y + state.h/2 - canvas.height / 2;

            ctx.save();
            ctx.translate(-camX, -camY);

            // Background
            ctx.fillStyle = '#0a0a12';
            ctx.fillRect(camX, camY, canvas.width, canvas.height);

            // Draw Tiles
            for (let r = 0; r < levelGrid.length; r++) {
                for (let c = 0; c < levelGrid[r].length; c++) {
                    const char = levelGrid[r][c];
                    const tx = c * TILE;
                    const ty = r * TILE;

                    if (char === '#') {
                        ctx.fillStyle = '#1a1a2e';
                        ctx.fillRect(tx, ty, TILE, TILE);
                        ctx.strokeStyle = '#16213e';
                        ctx.lineWidth = 1;
                        ctx.strokeRect(tx, ty, TILE, TILE);
                    } else if (char === 'S') {
                        ctx.fillStyle = '#4ecca3';
                        ctx.fillRect(tx, ty, TILE, TILE);
                    } else if (char === 'G') {
                        ctx.fillStyle = '#e94560';
                        ctx.fillRect(tx, ty, TILE, TILE);
                    }
                }
            }

            // Draw Player
            ctx.fillStyle = '#ffffff';
            ctx.shadowBlur = 15;
            ctx.shadowColor = '#ffffff';
            ctx.fillRect(state.x, state.y, state.w, state.h);
            ctx.shadowBlur = 0;

            ctx.restore();
        }

        function loop() {
            update();
            draw();
            requestAnimationFrame(loop);
        }

        initGame(currentSeed);
        loop();
    </script>
</body>
</html>"""
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_content)

def main():
    for var in ["API_BASE_URL", "API_KEY"]:
        if var not in os.environ:
            raise EnvironmentError(f"Missing required environment variable: {var}")

    write_index_html()

    seed = 42
    if os.path.exists("output.json"):
        try:
            with open("output.json", "r") as f:
                data = json.load(f)
                seed = data.get("seed", 42)
        except Exception:
            pass

    node_script = f"""
const {{ generateLevel }} = require('./level.js');
const fs = require('fs');
try {{
    const level = generateLevel({seed});
    const out = {{ seed: {seed}, level_grid: level }};
    fs.writeFileSync('output.json', JSON.stringify(out));
    console.log(JSON.stringify(out));
}} catch (e) {{
    console.error(e);
    process.exit(1);
}}
"""
    try:
        result = subprocess.run(["node", "-e", node_script], capture_output=True, text=True, check=True)
        print(result.stdout.strip())
    except subprocess.CalledProcessError as e:
        print(f"Node error: {e.stderr}")
        sys.exit(1)

    if os.path.exists("output.json"):
        with open("output.json", "r") as f:
            final_data = json.load(f)
            print(f"Created level with seed: {final_data.get('seed')}")

if __name__ == "__main__":
    main()
