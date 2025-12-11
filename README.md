# Proximity Map System – README

This project provides a real-time proximity visualization system for Teltonika FMC130 devices and BLE beacons, using a Flask backend and a Leaflet.js frontend.

# 📌 Overview

- **Backend:** Python Flask server running locally, receiving real-time telemetry from Flespi via an HTTP stream.
- **Frontend:** Leaflet.js interactive map showing the FMC130’s GPS position and BLE beacon proximity circles.
- **Tunneling:** Ngrok exposes the local Flask server to the internet so Flespi can deliver data reliably.

The system parses live data, preserves last-known beacon states for stability, and displays proximity-based circles around the FMC130.

---

## 🚀 Features

- Live FMC130 GPS tracking  
- BLE beacon detection  
- RSSI-to-distance conversion  
- Stable beacon visualization (beacons persist if next packet lacks BLE data)  
- Frontend refresh every 4 seconds  
- Ngrok-compatible backend endpoint  

---

## 📂 Project Structure

```
ble_proximity_realtime/
│
├── app.py                 # Flask backend
├── requirements.txt       # Python dependencies
│
├── templates/
│   └── index.html         # Frontend UI
│
└── static/
    ├── main.js            # Map logic, beacon rendering
    └── styles.css         # UI styling
```

---

## 🖥️ Running the Backend (Flask)

1. Install Python dependencies:
   ```
   pip install -r requirements.txt
   ```

2. Start the Flask server:
   ```
   python app.py
   ```

3. Start ngrok in a separate terminal:
   ```
   ngrok http 5000
   ```

4. Copy the HTTPS URL from ngrok and paste it into your Flespi HTTP Stream endpoint.

---

## 🌍 Using the Frontend

After Flask is running, open:

```
http://127.0.0.1:5000/map
```

You will see:
- FMC130 location  
- Beacon circles  
- Device and beacon counts  
- Last update timestamp  

---

## 🛠️ How Data Flows

1. FMC130 → sends telemetry to **Flespi**  
2. Flespi → forwards packets to **Flask server** via HTTP Stream  
3. Flask → extracts GPS & BLE data, stabilizes beacons  
4. Frontend → fetches `/data` every 4 seconds and updates map  

---

## ⚠ Limitations

- Distance estimation depends heavily on RSSI noise  
- FMC130 sends packets in intervals, so real-time smooth motion is limited  
- Beacons without recent RSSI updates retain last-known values until replaced  

---

## 📌 Future Improvements

- Rename beacons in UI  
- Alerts for geofence entry/exit  
- Asset lost/stolen flags  
- Tool-site reports  
- Dot-style visualization instead of proximity circles  
- UI redesign  