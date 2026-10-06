# Assault Fire Server Emulator

**Pinulongan:** [English](README.md) | [Tagalog](README-TL.md) | **Cebuano** | [简体中文](README-ZH-CN.md) | [Uban pang pinulongan](README-LANGUAGES.md)

Kini usa ka dili opisyal nga proyekto para sa pagpreserbar sa **Assault Fire PH** ug pag-emulate sa server niini. Dili kauban o gi-endorso sa Tencent, Level Up! Games, o sa orihinal nga tag-iya sa mga katungod ang proyekto ug ang mga server nga gidumala sa ubang tawo. Independenteng serbisyo ang mga server sa komunidad.

> **Gisuportahan ug gisulayan lamang:** Assault Fire PH **v1.0.0.24**. Walay orihinal nga file sa duwa dinhi. Kinahanglan naa na kay kaugalingong game files.

## Pinakasayon nga paagi sa pagsugod

1. Ibutang ang tibuok folder nga `af-emulator` sulod sa imong Assault Fire PH game folder.
2. I-right-click ang `START_ASSAULT_FIRE.ps1` ug pilia ang **Run with PowerShell**. Dawata ang Administrator prompt kon mogawas.
3. Awtomatikong susihon sa launcher ang game version ug setup, mag-andam sa lokal nga key files, ug mopasugod sa server, launch helper, ug game client.
4. Pag-login sa client. Kon makita na ang **START**, i-click kini aron mopadayon.

Sa normal nga one-click nga paagi, dili na kinahanglan nimo nga sugdan nga mano-mano ang server o patch tools. Dili kini mo-download o moapod-apod sa game files; gamiton ra niini ang imong kaugalingong lokal nga files. Kon dili motakdo ang version o dili ma-verify ang signature sa `TGame.exe` o `TCLS.dll`, hunong ug ayaw pugsa ang patch. Sa dili pa modagan, permanente nga i-patch sa launcher ang `TGame.exe` kon mapamatud-an ang patch, human maghimo og eksaktong backup nga `TGame.exe.bak`. Kon walay luwas nga code cave, magdugang kini og gamay nga executable PE section nga `.afdt` kon adunay bakanteng section-header slot; kon wala, dili usbon ang file.

## Manual nga setup ug developer

Tan-awa ang [kompletong English guide](README.md) para sa tanang lakang ug eksaktong command. Kinahanglan ang Windows, Python 3.10 o mas bag-o, ug kaugalingong kopya sa suportadong game version. Sa manual nga setup, hulata nga mahimong `UNLOCKED` ang preflight. Kon manual launch ang imong gigamit, ayaw i-click ang **START** hangtod makita ang `TCLS ARMED`. Ang `--server-only` para sa pag-host lang; dili niini giablihan ang local game-launch gate.

## Status ug paghangyo og tabang

Ang public stable baseline karon kay **v143b**. Nagtrabaho ang VERSION, AUTH, DIR, ROLE ug ZONE, room management, ug PvE match flow. Nagpadayon pa ang pag-ayo sa first-time nickname/account creation ug pipila ka social/progression features. Ang AP synchronization naggamit na sa native protocol path; wala nay local process-memory AP helper.

Kon mangayo kag tabang, ipadala ang screenshot sa error, unsang lakang ka, eksaktong command, `server/af_server_live.log`, ug game version. **Ayaw ipadala** ang `PRIVATE.PEM`, password, account credentials, token, o orihinal nga game files.

- [Project status](docs/STATUS.md) · [Launcher errors](docs/LAUNCHER_ERRORS.md) · [Setup notes](docs/VITAL_SETUP_NOTES.md) · [Dokumentasyon](docs/README.md)
- [Mga README sa tanang pinulongan](README-LANGUAGES.md)

**Lisensya:** MIT.
