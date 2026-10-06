# Assault Fire Server Emulator

**Langue :** [English](README.md) | [Tagalog](README-TL.md) | [Cebuano](README-CEB.md) | [简体中文](README-ZH-CN.md) | [Autres langues](README-LANGUAGES.md)

Projet non officiel consacré à la préservation d’**Assault Fire PH** et à l’émulation de son serveur. Le projet et les serveurs exploités par des tiers ne sont affiliés ni à Tencent, ni à Level Up! Games, ni aux ayants droit d’origine, et ne sont pas approuvés par eux. Les serveurs communautaires sont indépendants.

> **Seule version prise en charge et testée :** Assault Fire PH **v1.0.0.24**. Ce dépôt ne contient pas les fichiers du jeu. Vous devez posséder votre propre copie.

## La méthode la plus simple

1. Placez le dossier complet `af-emulator` dans le dossier du jeu Assault Fire PH.
2. Faites un clic droit sur `START_ASSAULT_FIRE.ps1`, puis choisissez **Run with PowerShell**. Autorisez les privilèges administrateur si Windows le demande.
3. Le lanceur vérifie la version et la configuration, prépare les clés locales, puis démarre le serveur, l’assistant de lancement et le client.
4. Connectez-vous au client. Lorsque le bouton **START** apparaît, cliquez dessus pour continuer.

Avec le parcours normal en un clic, vous n’avez pas à démarrer manuellement le serveur ni les outils de patch. Le script ne télécharge ni ne redistribue les fichiers du jeu : il utilise uniquement votre copie locale. Si la version ne correspond pas ou si la signature de `TGame.exe` ou `TCLS.dll` ne peut pas être vérifiée, arrêtez-vous et ne forcez pas le patch. Avant le lancement, le lanceur applique définitivement le correctif de date/heure vérifié à `TGame.exe`, après avoir créé une sauvegarde identique nommée `TGame.exe.bak`. Sans zone de code sûre, il ajoute une petite section PE exécutable `.afdt` uniquement si un emplacement libre existe dans la table des sections ; sinon, le fichier n’est pas modifié.

## Configuration manuelle et développement

Consultez le [guide complet en anglais](README.md) pour les étapes et commandes exactes. Il faut Windows, Python 3.10 ou plus récent et votre propre copie de la version prise en charge. En configuration manuelle, attendez que le contrôle préalable affiche `UNLOCKED`. Avec le lancement manuel, ne cliquez pas sur **START** avant que l’outil affiche `TCLS ARMED`. L’option `--server-only` sert uniquement à héberger le serveur et ne déverrouille pas le lancement local du jeu.

## État et aide

La base stable publique actuelle est **v143b**. Les chemins VERSION, AUTH, DIR, ROLE et ZONE, la gestion des salles et le flux des parties PvE fonctionnent. La création initiale du pseudo/compte et certaines fonctions sociales et de progression sont encore en cours. La synchronisation des AP utilise désormais uniquement le chemin protocolaire natif ; l'ancien helper local en mémoire du processus a été supprimé.

Pour demander de l’aide, envoyez une capture de l’erreur, l’étape suivie, la commande exacte, `server/af_server_live.log` et la version du jeu. **N’envoyez pas** `PRIVATE.PEM`, de mots de passe, d’identifiants de compte, de jetons ni de fichiers originaux du jeu.

- [État du projet](docs/STATUS.md) · [Erreurs du lanceur](docs/LAUNCHER_ERRORS.md) · [Notes de configuration](docs/VITAL_SETUP_NOTES.md) · [Index de la documentation](docs/README.md)
- [Tous les README par langue](README-LANGUAGES.md)

**Licence :** MIT.
