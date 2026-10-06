# Assault Fire Server Emulator

**Wika:** [English](README.md) | **Tagalog** | [Cebuano](README-CEB.md) | [简体中文](README-ZH-CN.md) | [Iba pang wika](README-LANGUAGES.md)

Isa itong hindi opisyal na proyekto para mapangalagaan ang **Assault Fire PH** at ma-emulate ang server nito. Hindi kaakibat, sponsored, o ineendorso ng Tencent, Level Up! Games, o ng mga orihinal na may-ari ng karapatan ang proyektong ito at ang mga server na pinapatakbo ng ibang tao. Hiwalay at independent ang mga community server.

> **Ito lamang ang supported at nasubok na bersyon:** Assault Fire PH **v1.0.0.24**. Walang kasamang game files ang repository na ito. Kailangan mayroon kang sarili mong mga file ng laro.

## Pinakamadaling paraan para magsimula

1. Ilagay ang buong folder na `af-emulator` sa loob ng folder ng Assault Fire PH game mo.
2. I-right-click ang `START_ASSAULT_FIRE.ps1`, pagkatapos piliin ang **Run with PowerShell**. Payagan ang Administrator access kung hingin ng Windows.
3. Awtomatikong susuriin ng launcher ang bersyon at setup, ihahanda ang mga lokal na key, at sisimulan ang server, launch helper, at game client.
4. Mag-login sa client. Kapag lumitaw na ang **START**, i-click ito para magpatuloy.

Sa normal na one-click setup, hindi mo kailangang mano-manong patakbuhin ang server o patch tools. Hindi nagda-download o namamahagi ng game files ang script; sarili mong lokal na files lang ang ginagamit nito. Kung hindi tugma ang bersyon o hindi ma-verify ang signature ng `TGame.exe` o `TCLS.dll`, huminto at huwag pilitin ang patch. Bago mag-launch, permanenteng inilalapat ng launcher ang beripikadong date/time patch sa `TGame.exe` at gumagawa muna ng eksaktong backup na `TGame.exe.bak`. Kung walang ligtas na code cave, nagdadagdag ito ng maliit na executable PE section na `.afdt` kung may bakanteng section-header slot; kung wala, hindi nito babaguhin ang file.

## Manual setup at para sa developer

Nasa [buong gabay sa English](README.md) ang lahat ng hakbang at eksaktong command. Kailangan ang Windows, Python 3.10 o mas bago, at sarili mong kopya ng supported na bersyon ng laro. Sa manual setup, hintaying magpakita ang preflight ng `UNLOCKED`. Kung mano-mano mong ilulunsad ang laro, huwag i-click ang **START** bago ipakita ng helper ang `TCLS ARMED`. Para lang sa pagho-host ang `--server-only`; hindi nito ino-unlock ang local game launch.

## Status at paghingi ng tulong

Ang kasalukuyang public stable baseline ay **v143b**. Gumagana ang VERSION, AUTH, DIR, ROLE at ZONE, pamamahala ng mga room, at PvE match flow. Ginagawa pa ang first-time nickname/account creation at ilang social/progression feature. Ang AP synchronization ay gumagamit na ng native protocol path; wala nang local process-memory AP helper.

Kapag humingi ng tulong, ipadala ang screenshot ng error, kung anong hakbang ang ginagawa mo, ang eksaktong command, `server/af_server_live.log`, at bersyon ng laro. **Huwag ipadala** ang `PRIVATE.PEM`, password, account credential, token, o orihinal na game file.

- [Status ng project](docs/STATUS.md) · [Mga error sa launcher](docs/LAUNCHER_ERRORS.md) · [Mahahalagang setup note](docs/VITAL_SETUP_NOTES.md) · [Index ng dokumentasyon](docs/README.md)
- [Lahat ng README ayon sa wika](README-LANGUAGES.md)

**Lisensya:** MIT.
