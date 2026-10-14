#!/bin/sh
# HELP: Claude Code sessions and permission prompts from the Mac
# ICON: app

. /opt/muos/script/var/func.sh

echo app >/tmp/act_go

APP_DIR="$(GET_VAR "device" "storage/rom/mount")/MUOS/application/.agentremote"
cd "$APP_DIR" || exit

SET_VAR "system" "foreground_process" "python3"

export SDL_HQ_SCALER="$(GET_VAR "device" "sdl/scaler")"
export SDL_ROTATION="$(GET_VAR "device" "sdl/rotation")"
export SDL_BLITTER_DISABLED="$(GET_VAR "device" "sdl/blitter_disabled")"

python3 agent_remote.py >"$APP_DIR/log.txt" 2>&1

unset SDL_HQ_SCALER SDL_ROTATION SDL_BLITTER_DISABLED
