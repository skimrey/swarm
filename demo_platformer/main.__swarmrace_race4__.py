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

def read_file_content(filename: str) -> str:
    """
    Reads the content of a specified file.
    """
    if filename == "output.json":
        try:
            with open(filename, 'r') as f:
                return f.read()
        except FileNotFoundError:
            return "{}"
    return ""

def main():
    """
    The main entry point. This function ensures all required game files
    exist before attempting to run the game logic.
    """
    print("--- Starting Game Applet Setup ---")
    
    # 1. Check for required environment variables (API credentials)
    required_env_vars = ["API_BASE_URL", "API_KEY"]
    for var in required_env_vars:
        if var not in os.environ:
            raise EnvironmentError(f"Missing required environment variable: {var}")

    # 2. Read potential output from a previous step (output.json)
    output_json_content = read_file_content("output.json")
    try:
        output_data = json.loads(output_json_content)
        print("Successfully loaded data from output.json.")
    except json.JSONDecodeError:
        print("Warning: Could not decode output.json. Starting with empty state.")
        output_data = {}
    except FileNotFoundError:
        print("Info: output.json not found. Starting with empty state.")
        output_data = {}

    # 3. Verification of required files for the game to function
    # The request specifies: index.html, level.js, physics.js
    required_files = ["index.html", "level.js", "physics.js"]
    all_present = True
    for f in required_files:
        if not os.path.exists(f):
            print(f"Error: Required file {f} is missing from the directory.")
            all_present = False
        else:
            print(f"Confirmed: {f} is present.")

    if not all_present:
        raise FileNotFoundError("One or more required game files are missing. Cannot proceed.")

    # 4. Execute the game logic via Node.js to generate the level
    # Since level.js uses 'require' and 'fs', it is intended for a Node environment.
    # We use a small wrapper to call the generateLevel function and capture its output.
    print("\n--- Executing Game Logic (Level Generation) ---")
    
    # We use a node script to invoke the logic in level.js
    # This ensures the 'require' and 'fs' calls inside level.js work as intended.
    node_wrapper = (
        "const { generateLevel } = require('./level.js'); "
        "try { "
        "  const seed = " + str(output_data.get('seed', 42)) + "; "
        "  const width = " + str(output_data.get('width', 60)) + "; "
        "  const height = " + str(output_data.get('height', 20)) + "; "
        "  generateLevel(seed, width, height); "
        "} catch (e) { "
        "  console.error(e.message); "
        "  process.exit(1); "
        "}"
    )

    try:
        process = subprocess.run(
            ["node", "-e", node_wrapper],
            capture_output=True,
            text=True,
            check=True
        )
        print("Node output:", process.stdout.strip())
        if process.stderr:
            print("Node stderr:", process.stderr.strip())
            
    except subprocess.CalledProcessError as e:
        print(f"Error during Node execution:\nSTDOUT: {e.stdout}\nSTDERR: {e.stderr}")
        raise RuntimeError("Failed to execute game logic via Node.js.")
    except FileNotFoundError:
        raise RuntimeError("Node.js is not installed or not in PATH.")

    # 5. Final verification of output.json after generation
    final_output_content = read_file_content("output.json")
    if not final_output_content or final_output_content == "{}":
        raise RuntimeError("Game logic completed but output.json was not populated with level data.")
    
    final_output_data = json.loads(final_output_content)
    if "level_grid" not in final_output_data:
        raise RuntimeError("Generated output.json is missing the 'level_grid' key.")

    print("\n--- Setup Complete ---")
    print("All required files are present and game logic has successfully generated the level.")
    print(f"Level Grid Size: {len(final_output_data['level_grid'])} rows")

if __name__ == "__main__":
    main()
