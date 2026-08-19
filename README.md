# Dashboard Cutting / Gutting — Raspberry Pi Zero 2W

## 0. Présentation générale

Ce projet fait tourner une **passerelle industrielle** sur une Raspberry Pi
Zero 2W pour la ligne Cutting/Gutting :

- **Modbus RTU** (RS485) vers les devices physiques (gutting, capteurs, etc.)
- **GPIO CP-IO22** pour la logique CIP (nettoyage en place)
- **MQTT** comme bus central : la RPi publie l'état complet en JSON et reçoit
  les commandes
- **Ecava IntegraXor (IGX)**, hébergé sur le serveur SCADA, qui ne connaît que
  **deux tags** (`machine_state_json` en lecture, `machine_command_json` en
  écriture) — tout le détail des machines passe dans le JSON, pas dans des
  tags Ecava individuels
- Un **dashboard opérateur Pygame** (`main.py` / `dashboard.py`), affiché en
  plein écran sans environnement de bureau (mode kiosque), qui montre les KPI
  de production (poissons/minute, Good/Bad/Belly, RPM, trips moteur, CIP,
  eau/électricité, météo, pauses)

Ce document couvre l'installation complète sur une RPi Zero 2W **vierge**
(OS fraîchement installé), puis l'utilisation du système une fois en place.

**Ordre du guide :**
1. Installation des paquets, environnement virtuel, test de `main.py`
2. Mode kiosque (démarrage automatique sans bureau)
3. Activation du port série pour le HAT RS485
4. Outils de développement / test
5. Utilisation et référence (SCADA, dashboard, dépannage)

---

## 1. Installation de base

Cible : Raspberry Pi Zero 2W, Raspberry Pi OS (Debian Bookworm), utilisateur
`lastra`, projet dans `~/dashboard-Cutting-Gutting`, venv `~/fish-venv`.

### 1.1 Pourquoi cette approche

Certaines libs (`pygame`, `gpiozero`) doivent être liées aux bibliothèques
natives du système (SDL2 avec support KMSDRM, GPIO avec `lgpio`) pour
fonctionner en mode kiosque. Un `pip install` classique dans un venv isolé
installe des wheels génériques **sans** ce support. À l'inverse, tout
installer au niveau système casse la reproductibilité et les contraintes de
version fines (ex. `pymodbus>=3.6,<4`).

**Solution retenue** : installer les libs à bindings natifs via `apt`, créer
le venv avec `--system-site-packages` pour qu'il les voie, et n'utiliser
`pip` que pour les libs pures Python où la version précise compte.

### 1.2 Mise à jour du système et paquets apt

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y \
  git \
  python3-venv \
  python3-pygame \
  python3-yaml \
  python3-serial \
  python3-paho-mqtt \
  python3-gpiozero \
  python3-lgpio
```

`python3-lgpio` est le pin factory recommandé par `gpiozero` sur Bookworm —
il évite le repli sur `NativeFactory` (expérimental) observé quand aucun pin
factory correct n'est disponible.

### 1.3 Récupération du projet

```bash
cd ~
git clone <url-du-repo> dashboard-Cutting-Gutting
cd ~/dashboard-Cutting-Gutting
```

(remplacer par la méthode réellement utilisée — clone git, `scp`, clé USB…)

### 1.4 Création du venv avec accès aux paquets système

```bash
python3 -m venv ~/fish-venv --system-site-packages
source ~/fish-venv/bin/activate
```

### 1.5 Libs restantes via pip (contrainte de version précise)

```bash
pip install --upgrade pip
pip install "pymodbus>=3.6,<4"
```

`pygame`, `PyYAML`, `pyserial`, `paho-mqtt`, `gpiozero` ne sont **pas**
réinstallés via pip : le venv les voit déjà depuis le système grâce à
`--system-site-packages`.

### 1.6 Accès GPIO / série / vidéo pour l'utilisateur

```bash
sudo usermod -aG dialout,video,render,gpio lastra
```

Se déconnecter/reconnecter (ou redémarrer) pour que les groupes prennent
effet :

```bash
groups lastra
# Doit inclure : dialout video render gpio
```

### 1.7 Vérification de l'installation

```bash
python3 -c "import pygame; print('pygame SDL:', pygame.get_sdl_version())"
python3 -c "from gpiozero import Device; print('pin factory:', Device.pin_factory)"
python3 -c "import pymodbus; print('pymodbus:', pymodbus.__version__)"
```

Attendu :
- SDL version alignée sur celle du système (pas une version isolée du venv)
- `pin factory: LGPIOFactory` (pas `NativeFactory`)
- `pymodbus` conforme à la contrainte `>=3.6,<4`

### 1.8 Test manuel de `main.py`

Avant de configurer le mode kiosque, vérifier que le programme démarre
correctement en console normale (bureau ou SSH avec affichage local) :

1. Vérifier/adapter `config.yaml` (port Modbus, devices activés, section
   `mqtt`, `weather`, `dashboard`…)
2. Lancer :
   ```bash
   cd ~/dashboard-Cutting-Gutting
   source ~/fish-venv/bin/activate
   python3 main.py --config config.yaml
   ```
3. Vérifier dans les logs : connexion Modbus, connexion MQTT, publication du
   JSON sur `factory/cutting-gutting/scada/state`.

Ce n'est qu'une fois ce test concluant qu'il faut passer au mode kiosque.

---

## 2. Mode kiosque (démarrage automatique sans bureau)

### 2.1 Démarrage en mode console (sans bureau)

```bash
sudo raspi-config
```
→ `System Options` → `Boot / Auto Login` → **`Console Autologin`**

```bash
sudo reboot
```

Après redémarrage, vérifier qu'aucun processus de bureau ne tourne :

```bash
htop
# Ne doivent PAS apparaître : Xwayland, labwc, wf-panel-pi, lxterminal
```

### 2.2 Accès aux périphériques graphiques

```bash
sudo usermod -aG video,render,gpio,i2c,spi lastra
```

(déjà fait en §1.6 pour `video`/`render`/`gpio` — cette commande ajoute en
plus `i2c`/`spi` si nécessaire pour d'autres périphériques)

Vérifier que les périphériques DRM existent :

```bash
ls -l /dev/dri
# Doit lister : card0, renderD128
```

### 2.3 Rendu KMSDRM

Déjà en place dans `dashboard.py` (haut du fichier, avant l'import de
`pygame`) :

```python
os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")
```

`setdefault` laisse la priorité à une variable d'environnement définie par le
service systemd si besoin, sans devoir modifier le code. Un repli automatique
vers `fbcon` est aussi prévu dans `run_dashboard()` si `kmsdrm` échoue à
l'initialisation (message loggé dans ce cas).

### 2.4 (Optionnel) Désactiver le partage d'écran headless

Raspberry Pi OS Bookworm démarre par défaut un compositeur Wayland headless +
VNC (`wayvnc`), même en mode console. À désactiver si aucun accès bureau à
distance n'est nécessaire (libère de la RAM/CPU) :

```bash
systemctl list-units | grep -i vnc
sudo systemctl disable --now wayvnc.service
```
(ajuster le nom exact du service selon le résultat de la première commande)

### 2.5 Service systemd

```bash
sudo nano /etc/systemd/system/dashboard.service
```

```ini
[Unit]
Description=Dashboard Cutting/Gutting Production
After=multi-user.target

[Service]
Type=simple
User=lastra
WorkingDirectory=/home/lastra/dashboard-Cutting-Gutting
ExecStart=/home/lastra/fish-venv/bin/python3 /home/lastra/dashboard-Cutting-Gutting/main.py
Restart=on-failure
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Point important : `ExecStart` pointe vers l'interpréteur du venv
(`~/fish-venv/bin/python3`), pas `/usr/bin/python3` — systemd n'a pas de
notion de `source activate`, il faut lui donner le bon binaire directement.

```bash
sudo systemctl daemon-reload
sudo systemctl enable dashboard.service
sudo systemctl start dashboard.service
```

### 2.6 Vérification finale

```bash
sudo systemctl status dashboard.service
journalctl -u dashboard.service -f
```

Logs attendus au démarrage :
```
[DISPLAY] pilote=KMSDRM résolution=1920x1080
[GPIO] ... ONLINE via gpiozero ...
```

```bash
htop
```
- Aucun `Xwayland` / `labwc` / `lxterminal`
- `python3 ... main.py` seul processus d'affichage significatif

Pour un redémarrage complet du service après une modification du code ou de
la config, sans reboot :

```bash
sudo systemctl restart dashboard.service
```

---

## 3. Activation du port série pour le HAT RS485

Le HAT RS485 utilise l'UART matériel de la RPi (broches GPIO14/TXD et
GPIO15/RXD). Sur une RPi vierge, le port série n'est ni activé, ni disponible
en UART complet (PL011) car il est par défaut assigné au Bluetooth.

### 3.1 Activer le matériel série via raspi-config

```bash
sudo raspi-config
```
→ `Interface Options` → `Serial Port`
- *"Would you like a login shell to be accessible over serial?"* → **No**
  (sinon un getty tourne sur le port et entre en conflit avec Modbus)
- *"Would you like the serial port hardware to be enabled?"* → **Yes**

Cela ajoute `enable_uart=1` dans `/boot/firmware/config.txt`.

### 3.2 Libérer l'UART complet (désactiver le Bluetooth)

Sur la RPi Zero 2W, l'UART matériel principal (PL011, `ttyAMA0`) est assigné
au Bluetooth par défaut ; seul le mini-UART (`ttyS0`, moins fiable à haut
débit) reste sur les broches GPIO. Pour avoir le PL011 complet sur les
broches (recommandé pour Modbus RTU) :

```bash
sudo nano /boot/firmware/config.txt
```

Ajouter à la fin :
```ini
dtoverlay=disable-bt
```

Puis désactiver le service qui gère le Bluetooth sur l'UART :

```bash
sudo systemctl disable hciuart
sudo reboot
```

### 3.3 Vérification après redémarrage

```bash
ls -l /dev/serial0
# Doit être un lien symbolique vers /dev/ttyAMA0

raspi-gpio get 14,15
# Doit montrer les GPIO 14/15 en fonction ALT0 (TXD0/RXD0)
```

### 3.4 Test de la liaison série

Sans device branché, test en boucle locale (jumper TX↔RX sur le connecteur,
HAT débranché) :

```bash
python3 -c "
import serial, time
s = serial.Serial('/dev/serial0', 9600, timeout=1)
s.write(b'test')
time.sleep(0.2)
print(s.read(10))
"
```

Avec le HAT RS485 branché et un device sur le bus, utiliser directement
`test_modbus_slave.py` (§4.2) pour valider la communication de bout en bout.

### 3.5 Configuration du HAT et de `config.yaml`

- Vérifier le cavalier/DIP switch de **résistance de terminaison 120 Ω** sur
  le HAT : à activer uniquement à l'extrémité physique du bus RS485.
- Si le HAT gère le sens (DE/RE) automatiquement (détection de flux), aucune
  broche GPIO supplémentaire n'est nécessaire. Si le sens est manuel, câbler
  et configurer la broche DE/RE dédiée.
- Dans `config.yaml`, pointer le port Modbus vers le port série activé :
  ```yaml
  modbus_port: /dev/serial0
  ```
- Vérifier que l'utilisateur `lastra` est bien dans le groupe `dialout`
  (fait en §1.6) pour avoir le droit d'ouvrir `/dev/serial0`.

---

## 4. Outils de développement / test

Ces scripts s'utilisent sur un **PC de développement** (pas sur la RPi), avec
un adaptateur USB-RS485 branché sur le même bus que les devices.

### 4.1 `simulator` — Simulateur Modbus RTU

### Sur Windows just ouvrir le .exe `simulator`

### le config.yaml dans `/dist` est le fichier de config ###

Simule en GUI tous les devices activés dans `config.yaml` qui ne sont pas dans
`REAL_DEVICES`. Permet de tester `main.py` sans avoir tous les appareils
physiques branchés.

<!-- **Configuration du simulator.py (haut du fichier)** :

```python
# Devices physiquement présents sur le bus — exclus du simulateur
REAL_DEVICES: set[str] = {"gutting_left"}
```

**Démarrage avec port série virtuel** (pour tester en parallèle avec
`main.py` sur la même machine, via `socat`) : -->

```bash
# Linux/RPi seulement
sudo apt install socat
socat -d -d pty,raw,echo=0 pty,raw,echo=0
# Affiche ex : /dev/pts/3  et  /dev/pts/4
# → simulateur sur /dev/pts/3, config.yaml modbus_port: /dev/pts/4
```

Dans la GUI :
- Champ **Port série** : adapter au port COM/tty de l'adaptateur USB-RS485
- **Input Registers** : injectés par le simulateur → lus par le maître
- **Holding Registers / Coils** : lecture seule, montrent ce que `main.py` a
  écrit
- Checkbox **Actif sur le bus** : décocher = le slave ne répond plus (simule
  un appareil absent)


> [!NOTE]
> **Si un device est deja present sur le bus Modbus, il faut le desactiver dans l'app sinon risque de collisions et il apparaitra comme hors ligne dans le dashboard**

### 4.2 `test_modbus_slave.py` — Lecture directe d'un device gutting

Lit en boucle toutes les 1 s les registres du device gutting (slave 3 par
défaut) et les affiche dans le terminal. Utile pour vérifier la communication
RS485 sans lancer `main.py`.

```bash
python test_modbus_slave.py
```

Adapter en tête de fichier si besoin :

```python
client = ModbusSerialClient(port="/dev/serial0", ...)  # ou "COM3" sur Windows
DEVICE_ID = 3  # adresse slave du device à tester
```

Sortie typique :
```
RPM blade/w1/w2 : 2450 / 1200 / 1180
Motor trip/on   : 0 / 1
Uptime (s)      : 3742
FW version      : 0x236
```

---

## 5. Utilisation et référence

### 5.1 Intégration Ecava — seulement deux tags JSON

**Tag d'état** (RPi → Ecava) :
```text
Tag IGX : machine_state_json
Topic   : factory/cutting-gutting/scada/state
Sens    : MQTT Subscriber → tag IGX
Type    : string
```

La RPi publie toutes les valeurs dans un seul JSON retained :

```json
{
  "timestamp": 1784700000,
  "rpi": {
    "cip": {
      "gutting_left": {
        "enable": true, "on_ms": 500, "off_ms": 8000, "output": false
      }
    }
  },
  "devices": {
    "gutting_left": {
      "connected": true,
      "values": {"rpm_blade": 2450, "motors_trip": 0},
      "parameters": {"eject_delay_ms": 200, "eject_enable": 1}
    }
  }
}
```

**Tag de commande** (Ecava → RPi) :
```text
Tag IGX : machine_command_json
Topic   : factory/cutting-gutting/scada/command
Sens    : tag IGX → MQTT Publisher
Type    : string
Retain  : false
QoS     : 1
```

Commande CIP :
```json
{"target": "cip", "device": "gutting_left",
 "parameters": {"enable": 1, "on_ms": 500, "off_ms": 8000},
 "timestamp": 1784700000000}
```

Commande Modbus :
```json
{"target": "modbus", "device": "gutting_left",
 "parameters": {"eject_enable": 1, "eject_delay_ms": 200},
 "timestamp": 1784700000000}
```

Confirmation RPi : `factory/cutting-gutting/scada/ack`

Le `timestamp` rend chaque valeur de commande différente, afin qu'Ecava
publie bien deux commandes successives même si elles ont les mêmes
paramètres.

### 5.2 Interface HTML (`ecava_machine_control.html`)

La page lit l'état normalement. Pour les commandes, IntegraXor doit encoder
le JSON en Base64 avant `setTag()` :

```javascript
getTag("machine_state_json")
setTag("machine_command_json", btoa(JSON.stringify(command)))
```

La RPi détecte et décode automatiquement `base64(JSON)`. Elle continue aussi
à accepter du JSON brut envoyé depuis MQTT Explorer ou un autre client MQTT.

Les clés visuelles `*_actual` et `*_cmd` présentes dans le JavaScript sont des
identifiants internes — elles ne sont **pas** des tags à créer dans Ecava.

### 5.3 Sécurité et Modbus

- commandes MQTT non-retained
- validation de chaque valeur avant écriture
- relecture Modbus après écriture
- un seul thread accède au RS485
- CIP Waveshare verrouillés OFF
- sorties CP-IO22 forcées OFF au démarrage et à l'arrêt
- Input Registers lus chaque seconde
- paramètres lus toutes les 10 secondes

### 5.4 Dashboard Pygame — production

L'écran privilégie les KPI opérateur : poissons/minute, personnes présentes,
Good, Bad et éjections Belly. Chaque Gutting possède sa courbe de
productivité glissante. Les trips moteur déclenchent un bandeau rouge et les
RPM restent visibles dans les cartes machine secondaires. La zone CIP compacte
ne montre que les trois sorties Raspberry Pi / CP-IO22. L'eau et
l'électricité sont regroupées en bas avec leurs icônes et consommations
jour/mois.

Le retour Cutting machine utilise provisoirement les entrées BCM20
(`cutting_motors_on`) et BCM21 (`cutting_motors_trip`) du CP-IO22, actives
HIGH. Le statut reste visible près des CIP et un Trip Cutting rejoint
immédiatement le bandeau d'alarme rouge global. Adapter les pins et la
polarité au câblage réel.

Le débit est calculé sur la variation des compteurs `fish_counter` pendant la
fenêtre `dashboard.productivity_window_s`. Pour afficher le personnel,
ajouter le registre `people_count` au `input_registers` du device choisi puis
renseigner `dashboard.people_device` et `dashboard.people_key`.

Les indicateurs Good, Bad et Belly sont affichés en pourcentage du nombre
total de poissons correspondant. Le compteur brut reste visible en petit sous
chaque pourcentage. Le même calcul est appliqué au total général et
séparément à chaque Gutting.

### 5.5 Compteurs eau / électricité

Deux devices provisoires sont fournis dans `config.yaml` : slaves 10 et 11,
désactivés par défaut. Le lecteur accepte `uint16`, `int16`, `uint32`,
`int32` et `float32`, avec `word_order`, `scale`, `offset` et `decimals`.
Remplacer les adresses/types par ceux des notices puis passer chaque device à
`enabled: true`.

Les valeurs jour/mois et les états de connexion apparaissent automatiquement
dans le dashboard et dans le JSON MQTT `factory/cutting-gutting/scada/state`.
La page Ecava les lit toujours via le seul tag `machine_state_json`.

### 5.6 Heure, météo et cadence par worker

Le dashboard affiche une grande horloge. Par défaut, le thread léger
`WeatherManager` appelle Open-Meteo toutes les 10 minutes avec les
coordonnées de la section `weather` et récupère `temperature_2m` et
`weather_code`. Aucune clé API n'est nécessaire. Les coordonnées fournies sont
celles de Taiping et doivent être remplacées si la machine se trouve
ailleurs.

La météo peut aussi être remplacée par MQTT sur le topic défini par
`mqtt.weather_topic` (par défaut `factory/cutting-gutting/weather`). Le
payload peut être `{"temperature_c":27.4,"condition":"cloudy"}` ou une
température simple. Un message retained est accepté pour disposer d'une
valeur au boot.

La cadence par worker est calculée en temps réel avec
`productivité_totale_poissons_minute / personnes_présentes`. Elle affiche
`--` si le compteur de personnes est absent ou égal à zéro. En attendant un
registre Modbus, le nombre peut être publié en retained sur
`factory/cutting-gutting/people_count` sous forme scalaire (`7`) ou JSON
(`{"people_count":7}`).

### 5.7 Pauses et remise à zéro

Les quatre pauses par défaut sont définies dans `schedule.breaks`. Ecava
envoie leur modification avec une commande `target: system` ; la RPi les
trie, les valide puis les sauvegarde dans `runtime_settings.json`. Pygame
affiche l'heure de la prochaine pause et le temps restant, en passant
automatiquement à la première pause du lendemain après la dernière pause.

Le Reset Ecava capture les compteurs courants comme offsets logiciels. Il
remet à zéro les statistiques affichées et les courbes, sans écrire dans les
registres Modbus et sans modifier les réglages CIP. Les offsets sont
persistants et également publiés dans le JSON MQTT d'état.

### 5.8 Dépannage rapide

| Symptôme | Cause probable | Action |
|---|---|---|
| `pygame.error: kmsdrm not available` en mode bureau | Compositeur détient déjà le périphérique DRM | Repasser en Console Autologin (§2.1) |
| `kmsdrm not available` même en console | pygame lié à une SDL2 embarquée (venv sans `--system-site-packages`) | Refaire §1.2–1.4 |
| `fbcon not available` aussi | `/dev/dri` absent, overlay KMS non activé | Vérifier `dtoverlay=vc4-kms-v3d` dans `/boot/firmware/config.txt` |
| `PinFactoryFallback` → `NativeFactory` | `lgpio`/`RPi.GPIO`/`pigpio` absents ou venv isolé | Installer `python3-lgpio` (§1.2) + recréer venv avec `--system-site-packages` |
| Pas de communication Modbus sur `/dev/serial0` | UART non activé ou toujours assigné au Bluetooth | Refaire §3.1–3.3, vérifier `dtoverlay=disable-bt` et `hciuart` désactivé |
| `PermissionError` sur `/dev/serial0` ou `/dev/ttyAMA0` | Utilisateur pas dans le groupe `dialout` | `sudo usermod -aG dialout lastra` puis se reconnecter |
| RAM qui grossit sur plusieurs jours | Fuite dans la logique de reconnexion Modbus/MQTT (hors `dashboard.py`) | Vérifier les boucles de retry dans `main.py` / le module d'état partagé |