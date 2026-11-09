// Scripted test driver for Play as Nabooru (development tool).
//
// Inert unless the environment variable NABOORU_TEST_SCRIPT names a script file. Then it replaces controller 1's
// input with the script, logs Link's animation state and Nabooru's move every frame, and asks an external capturer
// for screenshots (tools/run_test.ps1 launches the game in an isolated folder and does the capturing).
//
// Script commands, one per line ('#' starts a comment). Input tokens: buttons A B Z R L START CU CD CL CR DU DD DL DR,
// and "stick X Y" (-80..80, camera relative, Y up = forward).
//   waitplay                  wait until gameplay runs and the player can move
//   wait N                    neutral input for N frames
//   hold N <input>            hold the input for N frames
//   shots N EVERY NAME <input> hold the input for N frames, taking a screenshot every EVERY frames
//   shot NAME                 screenshot (gameplay pauses until it's taken)
//   log on|off                per-frame log to <out>/log.txt
//   mark TEXT                 write TEXT into the log
//   hit TYPE                  hit the player (0 light, 1 knockdown, 2 small knockback, 3 frozen, 4 shock)
//   health N                  set health in 1/16 hearts
//   yaw DEG                   turn the player to face DEG (0 = +Z)
//   pos X Y Z                 teleport the player
//   rise DY                   move the player up by DY (she falls back down)
//   cvar NAME VALUE           set an integer CVar (not saved)
//   warp HEX                  go to entrance HEX (follow with waitplay)
//   pose NAME FRAME           show frame FRAME of gPlayAsNabooru<NAME>Anim instead of mirroring Link; "pose off"
//   done                      write <out>/done and exit the game

#include <libultraship/bridge/consolevariablebridge.h>
#include "soh/Enhancements/game-interactor/GameInteractor.h"
#include "soh/ShipInit.hpp"
#include <ship/Context.h>
#include <ship/window/Window.h>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

extern "C" {
#include "z64.h"
#include "macros.h"
#include "functions.h"
#include "variables.h"
extern PlayState* gPlayState;
extern GameState* gGameState;
}

void PlayAsNabooru_DebugState(const char** move, float* frame);
void PlayAsNabooru_DebugForce(const char* name, float frame);

namespace {

enum class Kind { WaitPlay, Hold, Shot, Log, Mark, Hit, Health, Yaw, Pos, CVar, Pose, Warp, Rise, Done };

struct Step {
    Kind kind;
    int frames = 1;
    int every = 0;
    u16 buttons = 0;
    s8 stickX = 0;
    s8 stickY = 0;
    std::string text;
    float values[3] = { 0, 0, 0 };
};

std::vector<Step> sSteps;
size_t sStepIndex = 0;
int sStepFrame = 0;
std::string sOutDir;
FILE* sLog = nullptr;
bool sLogging = false;
bool sWaitingForShot = false;
int sShotWait = 0;
int sFrame = 0;
u16 sPrevButtons = 0;
s8 sPrevStickX = 0;
s8 sPrevStickY = 0;
Vec3f sLastPos;

u16 ButtonBit(const std::string& name) {
    static const std::pair<const char*, u16> kButtons[] = {
        { "A", BTN_A },         { "B", BTN_B },      { "Z", BTN_Z },      { "R", BTN_R },       { "L", BTN_L },
        { "START", BTN_START }, { "CU", BTN_CUP },   { "CD", BTN_CDOWN }, { "CL", BTN_CLEFT },  { "CR", BTN_CRIGHT },
        { "DU", BTN_DUP },      { "DD", BTN_DDOWN }, { "DL", BTN_DLEFT }, { "DR", BTN_DRIGHT },
    };
    for (auto& b : kButtons) {
        if (name == b.first) {
            return b.second;
        }
    }
    return 0;
}

void ParseInput(std::istringstream& in, Step& step) {
    std::string token;
    while (in >> token) {
        if (token == "stick") {
            int x = 0, y = 0;
            in >> x >> y;
            step.stickX = (s8)x;
            step.stickY = (s8)y;
        } else {
            step.buttons |= ButtonBit(token);
        }
    }
}

void LoadScript(const char* path) {
    std::ifstream file(path);
    std::string line;
    while (std::getline(file, line)) {
        line = line.substr(0, line.find('#'));
        std::istringstream in(line);
        std::string cmd;
        if (!(in >> cmd)) {
            continue;
        }
        Step step;
        if (cmd == "waitplay") {
            step.kind = Kind::WaitPlay;
        } else if (cmd == "wait") {
            step.kind = Kind::Hold;
            in >> step.frames;
        } else if (cmd == "hold") {
            step.kind = Kind::Hold;
            in >> step.frames;
            ParseInput(in, step);
        } else if (cmd == "shots") {
            step.kind = Kind::Hold;
            in >> step.frames >> step.every >> step.text;
            ParseInput(in, step);
        } else if (cmd == "shot") {
            step.kind = Kind::Shot;
            in >> step.text;
        } else if (cmd == "log") {
            step.kind = Kind::Log;
            in >> step.text;
        } else if (cmd == "mark") {
            step.kind = Kind::Mark;
            std::getline(in, step.text);
        } else if (cmd == "hit" || cmd == "health" || cmd == "yaw") {
            step.kind = (cmd == "hit") ? Kind::Hit : (cmd == "health") ? Kind::Health : Kind::Yaw;
            in >> step.values[0];
        } else if (cmd == "rise") {
            step.kind = Kind::Rise;
            in >> step.values[0];
        } else if (cmd == "pos") {
            step.kind = Kind::Pos;
            in >> step.values[0] >> step.values[1] >> step.values[2];
        } else if (cmd == "cvar") {
            step.kind = Kind::CVar;
            in >> step.text >> step.values[0];
        } else if (cmd == "warp") {
            step.kind = Kind::Warp;
            int entrance = 0;
            in >> std::hex >> entrance;
            step.values[0] = (float)entrance;
        } else if (cmd == "pose") {
            step.kind = Kind::Pose;
            in >> step.text >> step.values[0];
            if (step.text == "off") {
                step.text.clear();
            }
        } else if (cmd == "done") {
            step.kind = Kind::Done;
        } else {
            continue;
        }
        sSteps.push_back(step);
    }
}

const char* ShortAnimName(const void* anim) {
    const char* name = (const char*)anim;
    if (name == nullptr || strncmp(name, "__OTR__", 7) != 0) {
        return "?";
    }
    const char* slash = strrchr(name, '/');
    name = slash ? slash + 1 : name;
    return (strncmp(name, "gPlayerAnim_link_", 17) == 0) ? name + 17 : name;
}

Player* GetPlayer() {
    return (gPlayState != nullptr) ? GET_PLAYER(gPlayState) : nullptr;
}

void LogFrame() {
    Player* player = GetPlayer();
    if (sLog == nullptr || !sLogging || player == nullptr) {
        return;
    }
    const char* move = "-";
    float moveFrame = 0.0f;
    PlayAsNabooru_DebugState(&move, &moveFrame);
    SkelAnime* link = &player->skelAnime;
    SkelAnime* upper = &player->upperSkelAnime;
    fprintf(sLog,
            "%5d step%-3zu link %-34s %5.1f/%5.1f up %-26s %5.1f | nb %-22s %5.1f | y %7.1f vy %5.1f spd %5.1f "
            "st1 %08X st2 %08X bg %04X face %04X dir %04X mv %4.1f\n",
            sFrame, sStepIndex, ShortAnimName(link->animation), link->curFrame, link->endFrame,
            ShortAnimName(upper->animation), upper->curFrame, move, moveFrame, player->actor.world.pos.y,
            player->actor.velocity.y, player->actor.speedXZ, player->stateFlags1, player->stateFlags2,
            player->actor.bgCheckFlags, (u16)player->actor.shape.rot.y, (u16)player->actor.world.rot.y,
            sqrtf(SQ(player->actor.world.pos.x - sLastPos.x) + SQ(player->actor.world.pos.z - sLastPos.z)));
    sLastPos = player->actor.world.pos;
    fflush(sLog);
}

void RequestShot(const std::string& name) {
    std::ofstream(sOutDir + "/shot.req") << name;
    sWaitingForShot = true;
    sShotWait = 0;
}

bool PlayerReady() {
    Player* player = GetPlayer();
    return player != nullptr && gPlayState->transitionTrigger == TRANS_TRIGGER_OFF &&
           gPlayState->transitionMode == TRANS_MODE_OFF && !(player->stateFlags1 & PLAYER_STATE1_IN_CUTSCENE) &&
           player->actor.bgCheckFlags & BGCHECKFLAG_GROUND;
}

// Runs once per game frame, right after the real controller state was copied in and before the game state updates.
void OnFrameStart() {
    if (gGameState == nullptr) {
        return;
    }
    sFrame++;
    Input* input = &gGameState->input[0];
    u16 buttons = 0;
    s8 stickX = 0, stickY = 0;

    // While a screenshot is pending, hold gameplay still (frame advance) so the capture shows exactly this frame.
    if (sWaitingForShot) {
        bool taken = !std::filesystem::exists(sOutDir + "/shot.req");
        if (taken || ++sShotWait > 200) {
            sWaitingForShot = false;
        }
    }
    if (gPlayState != nullptr) {
        gPlayState->frameAdvCtx.enabled = sWaitingForShot;
    }

    while (!sWaitingForShot && sStepIndex < sSteps.size()) {
        Step& step = sSteps[sStepIndex];
        Player* player = GetPlayer();
        bool advance = true;
        switch (step.kind) {
            case Kind::WaitPlay:
                ++sStepFrame;
                advance = (PlayerReady() && sStepFrame > 20) || sStepFrame > 600;
                if (!advance) {
                    goto apply;
                }
                if (sStepFrame > 600) {
                    RequestShot("waitplay_timeout"); // never got control: show why
                }
                break;
            case Kind::Hold:
                buttons = step.buttons;
                stickX = step.stickX;
                stickY = step.stickY;
                if (step.every > 0 && (sStepFrame % step.every) == 0) {
                    char name[256];
                    snprintf(name, sizeof(name), "%s_%03d", step.text.c_str(), sStepFrame);
                    RequestShot(name);
                }
                LogFrame();
                advance = ++sStepFrame >= step.frames;
                if (!advance) {
                    goto apply;
                }
                sStepIndex++;
                sStepFrame = 0;
                goto apply;
            case Kind::Shot:
                RequestShot(step.text);
                break;
            case Kind::Log:
                sLogging = (step.text == "on");
                break;
            case Kind::Mark:
                if (sLog) {
                    fprintf(sLog, "----- %s\n", step.text.c_str());
                }
                break;
            case Kind::Hit:
                if (player) {
                    func_80837C0C(gPlayState, player, (s32)step.values[0], 4.0f, 5.0f, player->actor.shape.rot.y, 20);
                }
                break;
            case Kind::Health:
                gSaveContext.health = (s16)step.values[0];
                break;
            case Kind::Yaw:
                if (player) {
                    player->actor.shape.rot.y = player->actor.world.rot.y = player->yaw =
                        (s16)(step.values[0] * 0x8000 / 180.0f);
                }
                break;
            case Kind::Pos:
                if (player) {
                    player->actor.world.pos = { step.values[0], step.values[1], step.values[2] };
                    player->actor.prevPos = player->actor.home.pos = player->actor.world.pos;
                }
                break;
            case Kind::Rise:
                if (player) {
                    player->actor.world.pos.y += step.values[0];
                    player->actor.prevPos.y = player->actor.world.pos.y;
                }
                break;
            case Kind::CVar:
                CVarSetInteger(step.text.c_str(), (s32)step.values[0]);
                break;
            case Kind::Warp:
                if (gPlayState != nullptr) {
                    gPlayState->nextEntranceIndex = (s16)step.values[0];
                    gPlayState->transitionTrigger = TRANS_TRIGGER_START;
                    gPlayState->transitionType = TRANS_TYPE_INSTANT;
                }
                break;
            case Kind::Pose:
                PlayAsNabooru_DebugForce(step.text.c_str(), step.values[0]);
                break;
            case Kind::Done:
                std::ofstream(sOutDir + "/done") << sFrame;
                if (sLog) {
                    fclose(sLog);
                    sLog = nullptr;
                }
                exit(0);
        }
        if (advance) {
            sStepIndex++;
            sStepFrame = 0;
        }
    }

apply:
    input->prev.button = sPrevButtons;
    input->prev.stick_x = sPrevStickX;
    input->prev.stick_y = sPrevStickY;
    input->cur.button = buttons;
    input->cur.stick_x = input->rel.stick_x = stickX;
    input->cur.stick_y = input->rel.stick_y = stickY;
    u16 diff = sPrevButtons ^ buttons;
    input->press.button = buttons & diff;
    input->rel.button = sPrevButtons & diff;
    input->press.stick_x = stickX - sPrevStickX;
    input->press.stick_y = stickY - sPrevStickY;
    sPrevButtons = buttons;
    sPrevStickX = stickX;
    sPrevStickY = stickY;
}

void RegisterTestHarness() {
    const char* script = std::getenv("NABOORU_TEST_SCRIPT");
    if (script == nullptr || !sSteps.empty()) {
        return;
    }
    const char* out = std::getenv("NABOORU_TEST_OUT");
    sOutDir = (out != nullptr) ? out : ".";
    std::filesystem::create_directories(sOutDir);
    LoadScript(script);
    sLog = fopen((sOutDir + "/log.txt").c_str(), "w");
    GameInteractor::Instance->RegisterGameHook<GameInteractor::OnGameStateMainStart>(OnFrameStart);
}

} // namespace

static RegisterShipInitFunc initFunc(RegisterTestHarness, {});
