# Passerelle Cutting / Gutting — Raspberry Pi

La Raspberry Pi assure Modbus RTU, les GPIO CP-IO22, MQTT et le dashboard local
Pygame. L'interface SCADA est hébergée dans Ecava IGX sur le serveur.

## Installation

```bash
cd /home/lastra/dashboard-Cutting-Gutting
python3 -m venv .venv-2 --system-site-packages
source .venv-2/bin/activate
pip uninstall -y pygame-ce
pip install -r requirements.txt
sudo usermod -aG dialout,video,render,gpio lastra
```

Vérifier `config.yaml`, puis lancer :

```bash
.venv-2/bin/python main.py --config config.yaml
```

## Ecava : seulement deux tags JSON

### Tag d'état

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
        "enable": true,
        "on_ms": 500,
        "off_ms": 8000,
        "output": false
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

### Tag de commande

```text
Tag IGX : machine_command_json
Topic   : factory/cutting-gutting/scada/command
Sens    : tag IGX → MQTT Publisher
Type    : string
Retain  : false
QoS     : 1
```

Commande CIP générée par l'interface :

```json
{
  "target": "cip",
  "device": "gutting_left",
  "parameters": {"enable": 1, "on_ms": 500, "off_ms": 8000},
  "timestamp": 1784700000000
}
```

Commande Modbus :

```json
{
  "target": "modbus",
  "device": "gutting_left",
  "parameters": {"eject_enable": 1, "eject_delay_ms": 200},
  "timestamp": 1784700000000
}
```

Confirmation RPi :

```text
factory/cutting-gutting/scada/ack
```

Le `timestamp` rend chaque valeur de commande différente, afin qu'Ecava publie
également deux commandes successives ayant les mêmes paramètres.

## Interface HTML

La page `ecava_machine_control.html` lit l'état normalement. Pour les commandes,
IntegraXor doit encoder le JSON en Base64 avant `setTag()` :

```javascript
getTag("machine_state_json")
setTag("machine_command_json", btoa(JSON.stringify(command)))
```

La RPi détecte et décode automatiquement `base64(JSON)`. Elle continue aussi à
accepter du JSON brut envoyé depuis MQTT Explorer ou un autre client MQTT.

Les clés visuelles `*_actual` et `*_cmd` présentes dans son JavaScript sont des
identifiants internes. Elles ne sont pas des tags à créer dans Ecava.

## Sécurité et Modbus

- commandes MQTT non-retained ;
- validation de chaque valeur avant écriture ;
- relecture Modbus après écriture ;
- un seul thread accède au RS485 ;
- CIP Waveshare verrouillés OFF ;
- sorties CP-IO22 forcées OFF au démarrage et à l'arrêt ;
- Input Registers lus chaque seconde ;
- paramètres lus toutes les 10 secondes.

## Dashboard Pygame production

L'écran privilégie les KPI opérateur : poissons/minute, personnes présentes,
Good, Bad et éjections Belly. Chaque Gutting possède sa courbe de productivité
glissante. Les trips moteur déclenchent un bandeau rouge et les RPM restent
visibles dans les cartes machine secondaires. La zone CIP compacte ne montre
que les trois sorties Raspberry Pi / CP-IO22. L'eau et l'électricité sont
regroupées en bas avec leurs icônes et consommations jour/mois.

Le retour Cutting machine utilise provisoirement les entrées BCM20
(`cutting_motors_on`) et BCM21 (`cutting_motors_trip`) du CP-IO22, actives HIGH.
Le statut reste visible près des CIP et un Trip Cutting rejoint immédiatement
le bandeau d'alarme rouge global. Adapter les pins et la polarité au câblage.

Le débit est calculé sur la variation des compteurs `fish_counter` pendant la
fenêtre `dashboard.productivity_window_s`. Pour afficher le personnel, ajouter
le registre `people_count` au `input_registers` du device choisi puis renseigner
`dashboard.people_device` et `dashboard.people_key`.

## Electricity meter et Water meter

Deux devices provisoires sont fournis dans `config.yaml` : slaves 10 et 11,
désactivés par défaut. Le lecteur accepte `uint16`, `int16`, `uint32`, `int32`
et `float32`, avec `word_order`, `scale`, `offset` et `decimals`. Remplacer les
adresses/types par ceux des notices puis passer chaque device à `enabled: true`.

Les valeurs jour/mois et les états de connexion apparaissent automatiquement
dans le dashboard et dans le JSON MQTT `factory/cutting-gutting/scada/state`.
La page Ecava les lit toujours via le seul tag `machine_state_json`.

## Heure, météo et cadence par worker

Le dashboard affiche une grande horloge. Par défaut, le thread léger
`WeatherManager` appelle Open-Meteo toutes les 10 minutes avec les coordonnées
de la section `weather` et récupère `temperature_2m` et `weather_code`. Aucune
clé API n'est nécessaire. Les coordonnées fournies sont celles de Taiping et
doivent être remplacées si la machine se trouve ailleurs.

La météo peut aussi être remplacée par MQTT sur le topic défini par
`mqtt.weather_topic` (par défaut `factory/cutting-gutting/weather`). Le payload
peut être `{"temperature_c":27.4,"condition":"cloudy"}` ou une température
simple. Un message retained est accepté pour disposer d'une valeur au boot.

La cadence par worker est calculée en temps réel avec
`productivité_totale_poissons_minute / personnes_présentes`. Elle affiche `--`
si le compteur de personnes est absent ou égal à zéro. En attendant un registre
Modbus, le nombre peut être publié en retained sur
`factory/cutting-gutting/people_count` sous forme scalaire (`7`) ou JSON
(`{"people_count":7}`).

Les indicateurs Good, Bad et Belly sont affichés en pourcentage du nombre total
de poissons correspondant. Le compteur brut reste visible en petit sous chaque
pourcentage. Le même calcul est appliqué au total général et séparément à
chaque Gutting.

## Pauses et remise à zéro

Les quatre pauses par défaut sont définies dans `schedule.breaks`. Ecava envoie
leur modification avec une commande `target: system`; la RPi les trie, les
valide puis les sauvegarde dans `runtime_settings.json`. Pygame affiche l'heure
de la prochaine pause et le temps restant, en passant automatiquement à la
première pause du lendemain après la dernière pause.

Le Reset Ecava capture les compteurs courants comme offsets logiciels. Il remet
à zéro les statistiques affichées et les courbes, sans écrire dans les
registres Modbus et sans modifier les réglages CIP. Les offsets sont persistants
et également publiés dans le JSON MQTT d'état.

## Démarrage automatique

```bash
sudo cp machine-supervisor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now machine-supervisor.service
```
