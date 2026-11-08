# First test on Windows

This is a prototype. Use a separate superflat creative world for the next test. The bot can move a measured number of blocks, turn, sprint, jump, mine, and place blocks. In creative mode it can acquire registered block items into its hotbar; in survival it uses blocks already in its inventory. Crafting and pathfinding are not implemented yet.

## Install

1. Install Minecraft Java Edition **1.21.11** with [Fabric Loader](https://fabricmc.net/use/installer/).
2. Put [Fabric API for 1.21.11](https://modrinth.com/mod/fabric-api/versions?g=1.21.11), [Carpet 1.4.194](https://modrinth.com/mod/carpet/version/1.4.194), and `craftmind-mod-0.1.0.jar` in `mods`. Build the latter from `mod/` with `gradlew.bat build`; find it under `mod/build/libs/`.
3. Install Python 3.11 or newer. Open a terminal in `agent` and run `py -m craftmind.cli --mode scripted`.

## In-game check

1. Open a single-player world. `/craftmind status` should show `127.0.0.1:8765`.
2. Run `/craftmind spawn`. A player named `Craftmind` should appear.
3. Send `@Craftmind forward` in normal chat. The bot should move for about two seconds, stop, and reply in chat.
4. Try `@Craftmind jump`, `@Craftmind stop`, and `@Craftmind state`.

After updating `dev`, rebuild the JAR, replace the old file in `mods`, and restart Minecraft. Stop the old Python agent with Ctrl+C, then start the new one. Use `/craftmind spawn` to create the bot; `@Craftmind spawn` is a chat task, not the spawn command. In Ollama mode the model plans in short phases, with a default task limit of 128 actions (`--max-steps` changes it). The agent keeps only the last 16 actions in model context and stops repeated left-right turning before the limit. `@Craftmind stop` cancels the current task, including long movement.

If the bot does not spawn, check that Carpet and its `/player` command are available. If the agent reports a connection error, keep the world open and check `/craftmind status`. The error screenshot, `logs/latest.log`, and agent output will help debug the first run.

## Local model check

Install [Ollama](https://ollama.com/download/windows), then run `ollama pull qwen3:14b`. Stop the scripted agent and run `py -3.14 -m craftmind.cli --mode ollama --model qwen3:14b` from `agent`. On an empty stretch of a superflat creative world, try `@Craftmind place three iron blocks in a row in front of you`. The expected plan is one `place_line` action with `block_id=minecraft:iron_block` and `count=3`. All three blocks should appear in one row, and the bot's view should return to its previous angle. For a single specified cell, the model can use `place_at` with integer `x/y/z`; the mod chooses and aims at a visible supporting face. Move to an empty area before testing another row. If an action stops, capture its in-game reply and Python log lines containing `Model plan` and `Step`.
