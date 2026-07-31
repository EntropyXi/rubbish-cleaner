---
slug: d-drive-cleanup
status: awaiting-approval
intent: clear
review_required: false
pending-action: write .omo/plans/d-drive-cleanup.md
approach: D: drive junk cleanup plan - scan-based inventory (done), user-approved cleanup targets, pre/post free-space verification
note: scaffold-plan.mjs unavailable (oh-my-openagent plugin package was removed from .cache\opencode\packages by the c-drive-cleanup Todo 8; draft hand-built to the identical template)
---

# Draft: d-drive-cleanup

## Components (topology ledger)
| id | outcome (one line) | status | evidence path |
| --- | --- | --- | --- |
| driver-cleanup | D:\Driver delete all except 19_电子信息保修卡 (~7GB) | pending-approval | D:\Driver scan (25 entries, 7.0GB) |
| conda-cache | conda clean --all on D:\anaconda3 (reclaims 5-10GB of pkgs 16.7GB) | pending-approval | D:\anaconda3\pkgs scan (16.7GB, pkgs\cache 612MB) |
| root-archives | Delete 6 extracted-dup archives at D:\ root (~646MB) | pending-approval | D:\ root scan: MapInfo zip 484MB, clash zip 121MB, sakura zip 18.5MB, EPA zip 17MB, UsbEAm zip 4.2MB, 高数 rar 1.4MB |
| steam-leftovers | Delete 14 empty Steam game dirs (~0.8GB) | pending-approval | D:\SteamLibrary\steamapps\common scan (14 empty dirs: CSGO, BlackMythWukong, Dead Cells, DMC5, ReadyOrNot, WarThunder, L4D2, Ori, SleepingDogs, GarrysMod, Besiege, +4) |
| wegame-residue | Delete WeGameInstaller 372MB + tiny_cache 136MB + League Game\Logs 56MB | pending-approval | D:\Wegame scan |
| wechat-cache | Delete D:\WeiXin\xwechat_files\*\*\cache (~346MB) | pending-approval | D:\WeiXin scan (cache 346MB, 4755 files) |
| root-misc | Quarantine dinput8.dll + sdhdship.exe; delete mapinfo_install*.log + empty dirs | pending-approval | D:\ root scan (dinput8.dll, sdhdship.exe 35.8MB, mapinfo logs, empty: 新建文件夹(2)/Tencent Games/leidian/WSL/GameVideos) |
| not-touched | CRYSTALiA 7z 2.8GB KEPT; 3 launchers KEPT; BaiduNetdiskDownload KEPT | confirmed | user decisions 2026-07-31 |

## Open assumptions (announced defaults)
| assumption | adopted default | rationale | reversible? |
| --- | --- | --- | --- |
| dinput8.dll / sdhdship.exe | QUARANTINE (move to .omo\quarantine\d\), never delete | crack loader - deleting may break a game; user selected "谨慎" | yes (move back) |
| CRYSTALiA .7z | keep (user did NOT select) | user chose only root archives | N/A |
| launchers (Epic/Battle.net/Ubisoft) | keep (user selected 保留) | no games installed but user wants them | N/A |
| conda clean | run `conda clean --all` via D:\anaconda3\Scripts\conda.exe | safe, envs untouched, reversible (re-download) | yes |
| WeChat cache | delete cache subdirs only, NOT user chat files (xwechat_files msg/file dirs) | chat data is user data | mostly |
| empty dirs | delete only verified-empty top-level dirs | harmless shells | yes |

## Findings (cited - path:lines)
- D:\Driver = 25 entries, 7.0GB: 20 driver subdirs (Chipset, ME, IntelVGA, NV_*, AW/Intel Wlan+BT, LAN_RTK, Nahimic, ControlCenter, GNA, DTT, CCD, Senary, HID, IPF, ISST, SerialIO, PPM, DouDou) + 19_电子信息保修卡 + Auto_Install_Driver.bat + Ver.txt
- D:\anaconda3 = 44.5GB: pkgs 16.7GB (cache 612MB), envs 19.4GB (user envs - KEEP), Lib 5GB
- D:\ root: 6 archive/extracted dups ~646MB total; CRYSTALiA 7z 2.8GB (KEEP per user); stray dinput8.dll + sdhdship.exe 35.8MB; mapinfo_install.log + _install2.log; empty dirs 新建文件夹(2), Tencent Games, leidian, WSL\Ubuntu, GameVideos, _original_doc_backup (empty)
- D:\SteamLibrary\steamapps\common: 8 installed games (DS2 113.3GB, eFootball 48.6, Deadlock 33.7, WRC7 19, Aimlabs 18.6, FPSAimTrainer 6.9, BSide 3.4, Wallpaper Engine 1.7) + ~14 empty leftover dirs; workshop 2.7GB (KEEP - user mods)
- D:\Wegame: League 43.4GB (KEEP - game), WeGameInstaller 372MB, tiny_cache 136MB, League Game\Logs 55.8MB, duplicate mojibake empty dir 鑻遍泟鑱旂敾(26)
- D:\WeiXin: xwechat_files\*\*\cache 346MB (4755 files)
- D:\Ubisoft Game Launcher\cache 124MB - NOT in scope (user kept launcher; cache is small) - optional skip
- Launchers: Epic 1.4GB + Battle.net 0.5GB + Ubisoft 0.6GB - ALL KEPT per user
- Free space D: 100.2GB of 652.9GB

## Decisions (with rationale)
- Driver: delete all subdirs EXCEPT 19_电子信息保修卡; keep Auto_Install_Driver.bat? NO - delete it too (installer script only useful with drivers) - keep only 19_电子信息保修卡 (warranty info)
- conda: `conda clean --all` (purges tarballs, unused extracted pkgs, index cache, logs) - safe, existing envs keep working
- Root archives: delete the 6 zip/rar files ONLY (keep extracted folders)
- Steam: delete only the 14 empty dirs; NEVER touch installed game dirs or appmanifest files
- WeGame: delete WeGameInstaller dir + tiny_cache + League Game\Logs contents; KEEP League install
- WeChat: delete only cache dirs (recursive) under xwechat_files; KEEP msg/file/contact data
- Root misc: quarantine dinput8.dll + sdhdship.exe to .omo\quarantine\d\; delete mapinfo logs + verified-empty dirs; keep symlink DimensionToTsuLovers (points to CRYSTALiA game - user data)
- Verification: free-space before/after on D: per task; file-existence assertions; error CSV .omo/evidence/d-cleanup-errors.csv

## Scope IN
- D:\Driver (except 19_电子信息保修卡)
- D:\anaconda3: conda clean --all
- D:\ root archives: MapInfo zip, clash.verge zip, sakura zip, EPA.zip, UsbEAm zip, 2016-2025 rar
- D:\SteamLibrary\steamapps\common empty dirs (verified 0 files each)
- D:\Wegame: WeGameInstaller, tiny_cache, League Game\Logs, mojibake empty dir
- D:\WeiXin cache dirs
- D:\ root: quarantine dinput8.dll + sdhdship.exe; delete mapinfo logs; delete empty dirs (新建文件夹(2), Tencent Games, leidian, WSL\Ubuntu, GameVideos, _original_doc_backup if empty)
- Report: .omo/evidence/d-summary.md

## Scope OUT (Must NOT have)
- CRYSTALiA .7z (user kept); all 3 launchers (user kept); BaiduNetdiskDownload; BaiduNetdisk app; anaconda3\envs + Lib (user envs); installed games (League, Steam games, Apex, MDPro3, MuMu vms); workshop mods; WeChat chat files; D:\Driver\19_电子信息保修卡; DimensionToTsuLovers symlink; user docs/study files; .claude; System Volume Information; $RECYCLE.BIN (0.1MB trivial - leave)
- Nothing in the above list; default = list-and-report, delete only on explicit approval

## Open questions
ALL RESOLVED (2026-07-31):
1. Driver -> delete drivers, keep 19_电子信息保修卡
2. conda -> clean --all
3. launchers -> KEEP all three
4. root archives -> delete the 6 (CRYSTALiA kept)
5. misc -> quarantine DLLs/exe (谨慎), delete logs + empty dirs, WeChat cache, Steam/WeGame residue

## Approval gate
status: awaiting-approval
Approach: 6-task D-drive cleanup plan (draft originally 5; pre-flight baseline split into its own task 1 after Metis review) executed by worker session:
  - Task 1: pre-flight baseline D: + conda check + Steam game inventory + WSL registration check
  - Task 2: Driver cleanup (all except 19_电子信息保修卡) + quarantine dinput8.dll/sdhdship.exe + delete root archives/logs/empty dirs (junction-aware)
  - Task 3: conda clean --all
  - Task 4: Steam empty dirs (from inventory) + WeGame residue
  - Task 5: WeChat cache
  - Task 6: verification + d-summary.md
Next action on approval: write .omo/plans/d-drive-cleanup.md (hand-built, scaffold script unavailable), run Metis gap analysis (DONE - 3 MAJOR + 5 MINOR all fixed), append todos.
