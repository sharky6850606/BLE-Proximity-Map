const FETCH_INTERVAL_MS = 4000;

let map;
let deviceMarkers = {};   // ident -> Leaflet marker
let beaconCircles = {};   // beaconKey -> Leaflet circle
let beaconPrevState = {}; // for in/out detection
let notifications = [];
let reports = [];
let unreadCount = 0;
let heatLayer = null;

let currentBeaconNames = {};
let lastDevices = [];
let lastBeaconsAgg = [];
let currentDeviceFilter = '';   // '' = all devices
let deviceColors = {};          // ident -> color from backend

let renameContext = null;       // { type: 'device'|'beacon', id: string }


// ---- Map init ----
function initMap() {
  map = L.map('map').setView([-13.85, -171.75], 15); // default centre (adjust as needed)

  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap'
  }).addTo(map);
}


// ---- Fetch + update loop ----

async function fetchAndUpdateMapData() {
  try {
    const resp = await fetch('/data');
    if (!resp.ok) {
      console.error('Failed to fetch /data', resp.status);
      return;
    }
    const payload = await resp.json();
    const devices = payload.devices || [];
    const beaconNames = payload.beacon_names || {};

    currentBeaconNames = beaconNames;
    lastDevices = devices;

    // Extract daily report if present
    const dailyReport = devices.find(d => d && d.ident === 'DAILY_REPORT');
    if (dailyReport && dailyReport.report) {
      addDailyReport(dailyReport);
    }

    // Build aggregated beacon list (across devices)
    const aggBeacons = aggregateBeacons(devices, beaconNames);

    // in/out detection
    aggBeacons.forEach(b => {
      const key = `${b.deviceIdent || 'unknown'}::${b.id}`;
      const dist = b.distance ?? 9999;
      const prev = beaconPrevState[key] || 'unknown';
      const nowState = dist <= 3 ? 'in' : 'out';

      if (prev !== nowState) {
        beaconPrevState[key] = nowState;
        if (nowState === 'out') {
          addNotification('left', b.name || b.id, b.last_seen, dist);
        } else if (nowState === 'in' && prev !== 'unknown') {
          addNotification('in', b.name || b.id, b.last_seen, dist);
        }
      }
    });

    lastBeaconsAgg = aggBeacons;
    // ---- Update summary sidebar ----
    const goodDevices = devices.filter(d => d.ident !== "DAILY_REPORT");
    document.getElementById("summary-devices").textContent = goodDevices.length;

    document.getElementById("summary-beacons").textContent = aggBeacons.length;

    let latestTS = "-";
    goodDevices.forEach(d => {
        if (d.timestamp && d.timestamp !== "-" && d.timestamp !== null) {
            latestTS = d.timestamp;
        }
    });


    updateMap(devices, aggBeacons);
    updateSidebar(devices, beaconNames);
  } catch (e) {
    console.error('Error in fetchAndUpdateMapData', e);
  }
}

function startPolling() {
  fetchAndUpdateMapData();
  setInterval(fetchAndUpdateMapData, FETCH_INTERVAL_MS);
}


// ---- Aggregate beacons across devices ----

function aggregateBeacons(devices, beaconNames) {
  const result = [];
  devices.forEach(d => {
    if (!d || !d.beacons || d.ident === 'DAILY_REPORT') return;
    (d.beacons || []).forEach(b => {
      const id = b.id;
      const name = beaconNames[id] || b.name || id;
      result.push({
        id,
        name,
        deviceIdent: d.ident,
        deviceName: d.name || d.ident,
        deviceColor: d.color || '#3b82f6',
        distance: b.distance,
        last_seen: b.last_seen,
        rssi: b.rssi,
        lat: d.lat,
        lon: d.lon
      });
    });
  });
  return result;
}


// ---- Map rendering ----

function getDeviceColor(ident, fallback) {
  if (deviceColors[ident]) return deviceColors[ident];
  if (fallback) {
    deviceColors[ident] = fallback;
    return fallback;
  }
  deviceColors[ident] = '#3b82f6';
  return deviceColors[ident];
}

function clearMapLayers() {
  Object.values(deviceMarkers).forEach(m => map.removeLayer(m));
  Object.values(beaconCircles).forEach(c => map.removeLayer(c));
  deviceMarkers = {};
  beaconCircles = {};
}


// ---- Device Pin Icon (Colored) ----
function makeDeviceIcon(color) {
  return L.icon({
    iconUrl: `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(`
      <svg xmlns="http://www.w3.org/2000/svg" width="32" height="48" viewBox="0 0 32 48">
        <path fill="${color}" stroke="white" stroke-width="2"
          d="M16 0C8 0 2 6 2 14c0 11 14 34 14 34s14-23 14-34C30 6 24 0 16 0z"/>
        <circle cx="16" cy="14" r="6" fill="white"/>
      </svg>
    `)}`,
    iconSize:[32,48],
    iconAnchor:[16,48],
    popupAnchor:[0,-48]
  });
}

function updateMap(devices, aggBeacons) {
  if (!map) return;

  clearMapLayers();

  const bounds = [];

  
  // Draw devices as pin markers
  devices.forEach(d => {
    if (!d || d.ident === 'DAILY_REPORT') return;
    if (currentDeviceFilter && d.ident !== currentDeviceFilter) return;
    if (d.lat == null || d.lon == null) return;

    const color = getDeviceColor(d.ident, d.color);
    const latlng = [d.lat, d.lon];

    const tooltipHtml = `
      <div style="font-size:0.8rem;">
        <div><strong>${d.name || d.ident}</strong></div>
        <div>ID: ${d.ident}</div>
        <div>Last: ${d.timestamp || '-'}</div>
      </div>
    `;

    const icon = makeDeviceIcon(color);
    const marker = L.marker(latlng, { icon });

    marker.bindTooltip(tooltipHtml, {
      direction: 'top',
      sticky: true
    });

    marker.on('click', () => {
      setDeviceFilter(d.ident);
    });

    marker.addTo(map);
    deviceMarkers[d.ident] = marker;
    bounds.push(latlng);
  });


  // Draw beacons as circles around their device positions
  aggBeacons.forEach(b => {
    if (!b || b.lat == null || b.lon == null) return;
    if (currentDeviceFilter && b.deviceIdent !== currentDeviceFilter) return;

    const color = getDeviceColor(b.deviceIdent, b.deviceColor || '#22c55e');
    const latlng = [b.lat, b.lon];

    const circle = L.circle(latlng, {
      radius: Math.max(5, (b.distance || 1) * 2 + (([...b.id].reduce((a,c)=>a+c.charCodeAt(0),0) % 3) * 3)),
      weight: 2,
      color,
      fillColor: color,
      fillOpacity: 0.15
    });

    const tooltipHtml = `
      <div style="font-size:0.8rem;">
        <div><strong>${b.name || b.id}</strong></div>
        <div>ID: ${b.id}</div>
        <div>Device: ${b.deviceName}</div>
        <div>Distance: ${b.distance != null ? b.distance.toFixed(2) + ' m' : '-'}</div>
        <div>Last seen: ${b.last_seen || '-'}</div>
      </div>
    `;
    circle.bindTooltip(tooltipHtml, { direction: 'top', sticky: true });

    circle.addTo(map);
    beaconCircles[`${b.deviceIdent}::${b.id}`] = circle;
  });

  if (bounds.length > 0) {
    const latLngBounds = L.latLngBounds(bounds);
    map.fitBounds(latLngBounds.pad(0.2));
  }
}

function setDeviceFilter(ident) {
  if (currentDeviceFilter === ident) {
    currentDeviceFilter = '';
  } else {
    currentDeviceFilter = ident;
  }
  updateMap(lastDevices, lastBeaconsAgg);
  updateSidebar(lastDevices, currentBeaconNames);
}


// ---- Sidebar (Option C: colored device blocks with beacons) ----

function updateSidebar(devices, beaconNames) {
  const container = document.getElementById('device-list');
  if (!container) return;

  container.innerHTML = '';

  const headerRow = document.createElement('div');
  headerRow.className = 'devices-header-row';
  headerRow.innerHTML = `
    <div class="devices-header-left">
      <span class="devices-header-title">Devices</span>
    </div>
    <div class="devices-header-right">
      <button id="show-all-devices-btn" class="pill-btn ${currentDeviceFilter ? '' : 'pill-btn-active'}">
        All devices
      </button>
    </div>
  `;
  container.appendChild(headerRow);

  const allBtn = headerRow.querySelector('#show-all-devices-btn');
  if (allBtn) {
    allBtn.addEventListener('click', () => {
      currentDeviceFilter = '';
      updateMap(lastDevices, lastBeaconsAgg);
      updateSidebar(lastDevices, beaconNames);
    });
  }

  const visibleDevices = devices.filter(d => d && d.ident !== 'DAILY_REPORT');

  visibleDevices.forEach(d => {
    const ident = d.ident;
    const color = getDeviceColor(ident, d.color || '#3b82f6');
    const name = d.name || ident;

    if (currentDeviceFilter && ident !== currentDeviceFilter) {
      // Still show greyed out? For now, hide others completely.
      return;
    }

    const deviceBlock = document.createElement('div');
    deviceBlock.className = 'device-block';

    const beaconsForDevice = (d.beacons || []).map(b => {
      const id = b.id;
      const bName = beaconNames[id] || b.name || id;
      return {
        id,
        name: bName,
        distance: b.distance,
        last_seen: b.last_seen
      };
    });

    deviceBlock.innerHTML = `
      <div class="device-block-header" data-device-ident="${ident}">
        <div class="device-color-swatch" style="background:${color};"></div>
        <div class="device-block-main">
          <div class="device-block-title-row">
            <span class="device-block-title">${name}</span>
            <button
              class="icon-button rename-device-btn"
              data-device-id="${ident}"
              data-current-name="${name}"
              title="Rename device"
            >
              ✏
            </button>
          </div>
          <div class="device-block-sub">
            <span class="device-block-id">${ident}</span>
            <span class="device-block-time">${d.timestamp || ''}</span>
          </div>
        </div>
      </div>
      <div class="device-beacons-list">
        ${
          beaconsForDevice.length === 0
            ? '<div class="device-empty">No beacons</div>'
            : beaconsForDevice
                .map(
                  b => `
          <div class="beacon-row" data-beacon-id="${b.id}">
            <span class="beacon-color-dot" style="background:${color};"></span>
            <div class="beacon-info-block">
              <div class="beacon-name">
                ${b.name}
                ${b.distance != null ? `<span class="beacon-distance">(${b.distance.toFixed(2)} m)</span>` : ''}
              </div>
              <div class="beacon-id">${b.id}</div>
            </div>
            <button
              class="icon-button rename-beacon-btn"
              data-beacon-id="${b.id}"
              data-current-name="${b.name}"
              title="Rename beacon"
            >
              ✏
            </button>
          </div>
        `
                )
                .join('')
        }
      </div>
    `;

    // Clicking the device header focuses that device on map
    const header = deviceBlock.querySelector('.device-block-header');
    if (header) {
      header.addEventListener('click', () => {
        setDeviceFilter(ident);
      });
    }

    // Clicking a beacon row centers on that device + beacon
    const beaconRows = deviceBlock.querySelectorAll('.beacon-row');
    beaconRows.forEach(row => {
      row.addEventListener('click', e => {
        // ignore clicks that were for the pencil button
        if (e.target.closest('.rename-beacon-btn')) return;
        currentDeviceFilter = ident;
        updateMap(lastDevices, lastBeaconsAgg);
      });
    });

    container.appendChild(deviceBlock);
  });
}


// ---- Notifications ----

function addNotification(type, beaconName, eventTime, distance) {
  const timeStr = eventTime || '-';
  const msg = {
    type,
    name: beaconName,
    time: timeStr,
    distance: distance
  };
  notifications.push(msg);

  try {
    fetch('/api/notifications', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(msg)
    });
  } catch (e) {
    console.error('Failed to send notification to server', e);
  }

  unreadCount += 1;
  updateNotificationBadge();
  renderNotificationsList();
  showToast(msg);
}

function updateNotificationBadge() {
  const badge = document.getElementById('notifications-badge');
  if (!badge) return;
  badge.textContent = unreadCount > 0 ? String(unreadCount) : '';
}

function renderNotificationsList() {
  const list = document.getElementById('notifications-list');
  if (!list) return;
  list.innerHTML = '';

  notifications.slice().reverse().forEach(n => {
    const item = document.createElement('div');
    item.className = 'notification-item';
    item.innerHTML = `
      <span class="time">${n.time}</span>
      <span class="type-${n.type === 'left' ? 'left' : 'in'}">${n.type.toUpperCase()}</span>
      <span class="name">${n.name}</span>
      ${
        n.distance != null
          ? `<span class="distance">(${n.distance.toFixed(2)} m)</span>`
          : ''
      }
    `;
    list.appendChild(item);
  });
}

function showToast(msg) {
  const cont = document.getElementById('toast-container');
  if (!cont) return;
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = `${msg.type.toUpperCase()}: ${msg.name}`;
  cont.appendChild(el);
  setTimeout(() => {
    cont.removeChild(el);
  }, 4000);
}




function setupNotificationsUI() {
  const notifButton = document.getElementById('notif-button');
  const panel = document.getElementById('notifications-panel');
  const closeBtn = document.getElementById('notif-close');
  const clearBtn = document.getElementById('notif-clear');

  if (!panel) return;

  const hidePanel = () => {
    panel.classList.add('hidden');
  };

  const openPanel = () => {
    panel.classList.remove('hidden');
    // when user views panel, reset unread counter
    unreadCount = 0;
    updateNotificationBadge();
  };

  const togglePanel = () => {
    if (panel.classList.contains('hidden')) {
      openPanel();
    } else {
      hidePanel();
    }
  };

  if (notifButton) {
    notifButton.addEventListener('click', togglePanel);
  }
  if (closeBtn) {
    closeBtn.addEventListener('click', hidePanel);
  }
  if (clearBtn) {
    clearBtn.addEventListener('click', () => {
      notifications = [];
      renderNotificationsList();
      unreadCount = 0;
      updateNotificationBadge();
    });
  }
}

// ---- Rename modal ----

function openRenameModal(type, id, currentName) {
  renameContext = { type, id };

  const backdrop = document.getElementById('rename-backdrop');
  const modal = document.getElementById('rename-modal');
  const input = document.getElementById('rename-modal-input');
  const title = document.getElementById('rename-modal-title');

  if (!backdrop || !modal || !input || !title) return;

  title.textContent = type === 'device' ? 'Rename device' : 'Rename beacon';
  input.value = currentName || '';
  modal.classList.remove('hidden');
  backdrop.classList.remove('hidden');
  input.focus();
  input.select();
}

function closeRenameModal() {
  const backdrop = document.getElementById('rename-backdrop');
  const modal = document.getElementById('rename-modal');
  if (backdrop) backdrop.classList.add('hidden');
  if (modal) modal.classList.add('hidden');
  renameContext = null;
}

async function saveRenameModal() {
  if (!renameContext) return;
  const input = document.getElementById('rename-modal-input');
  const newName = (input?.value || '').trim();
  if (!newName) {
    closeRenameModal();
    return;
  }

  try {
    if (renameContext.type === 'device') {
      await fetch('/rename_device', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          device_id: renameContext.id,
          new_name: newName
        })
      });
    } else {
      await fetch('/rename', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          beacon_id: renameContext.id,
          new_name: newName
        })
      });
    }
  } catch (e) {
    console.error('Rename failed', e);
  }

  closeRenameModal();
  // refresh to pick up new names
  fetchAndUpdateMapData();
}

function setupRenameModalHandlers() {
  const backdrop = document.getElementById('rename-backdrop');
  const cancelBtn = document.getElementById('rename-modal-cancel');
  const saveBtn = document.getElementById('rename-modal-save');
  const input = document.getElementById('rename-modal-input');

  if (backdrop) {
    backdrop.addEventListener('click', closeRenameModal);
  }
  if (cancelBtn) {
    cancelBtn.addEventListener('click', closeRenameModal);
  }
  if (saveBtn) {
    saveBtn.addEventListener('click', saveRenameModal);
  }
  if (input) {
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter') {
        e.preventDefault();
        saveRenameModal();
      } else if (e.key === 'Escape') {
        e.preventDefault();
        closeRenameModal();
      }
    });
  }

  // Pencil icon delegation
  document.addEventListener('click', e => {
    const devBtn = e.target.closest('.rename-device-btn');
    if (devBtn) {
      const id = devBtn.getAttribute('data-device-id');
      const currentName = devBtn.getAttribute('data-current-name') || id;
      openRenameModal('device', id, currentName);
      return;
    }

    const beaconBtn = e.target.closest('.rename-beacon-btn');
    if (beaconBtn) {
      const id = beaconBtn.getAttribute('data-beacon-id');
      const currentName = beaconBtn.getAttribute('data-current-name') || id;
      openRenameModal('beacon', id, currentName);
      return;
    }
  });
}


// ---- Menu drawer (existing) ----

function setupMenu() {
  const btn = document.getElementById('menu-button');
  const panel = document.getElementById('menu-panel');
  const overlay = document.getElementById('menu-overlay');
  const closeBtn = document.getElementById('menu-close');

  if (!btn || !panel || !overlay || !closeBtn) {
    return;
  }

  function openMenu() {
    panel.classList.remove('hidden');
    panel.classList.add('open');
    overlay.classList.remove('hidden');
  }

  function closeMenu() {
    panel.classList.remove('open');
    panel.classList.add('hidden');
    overlay.classList.add('hidden');
  }

  btn.addEventListener('click', openMenu);
  closeBtn.addEventListener('click', closeMenu);
  overlay.addEventListener('click', closeMenu);

  const downloadLatestBtn = document.getElementById('menu-download-latest');
  const reportsHistoryBtn = document.getElementById('menu-reports-history');
  const notifHistoryBtn = document.getElementById('menu-notif-history');
  const activityReportsBtn = document.getElementById('menu-activity-reports');

  function goTo(url) {
    closeMenu();
    window.location.href = url;
  }

  if (downloadLatestBtn) {
    downloadLatestBtn.addEventListener('click', () => goTo('/download/latest-report'));
  }
  if (reportsHistoryBtn) {
    reportsHistoryBtn.addEventListener('click', () => goTo('/reports/history'));
  }
  if (notifHistoryBtn) {
    notifHistoryBtn.addEventListener('click', () => goTo('/notifications/history'));
  }
  if (activityReportsBtn) {
    activityReportsBtn.addEventListener('click', () => goTo('/activity-reports'));
  }
}


// ---- Init ----

document.addEventListener('DOMContentLoaded', () => {
  initMap();
  setupMenu();
  setupRenameModalHandlers();
  setupNotificationsUI();
  startPolling();
});