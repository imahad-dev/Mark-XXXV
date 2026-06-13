/* ===================================================================
   app.js – JARVIS MARK XXXV Dashboard Controller
   
   Responsibilities:
   - Real-time clock & greeting
   - System data polling from Python backend
   - Theme switching (JARVIS / FRIDAY / ULTRON)
   - Three.js orb with per-theme color mapping
   - Log & state management
   =================================================================== */

// ---------------------------------------------------------------------------
// DOM REFERENCES
// ---------------------------------------------------------------------------
const $ = (id) => document.getElementById(id);

const dom = {
    clock:       $('clock-display'),
    date:        $('date-display'),
    greeting:    $('greeting-text'),
    subGreeting: $('sub-greeting'),
    signature:   $('signature-text'),
    logo:        $('logo-text'),
    logHeader:   $('log-header'),
    aiLabel:     $('ai-name-label'),

    // Left column
    cityName:    $('city-name'),
    regionText:  $('region-text'),
    coords:      $('coords-display'),
    battery:     $('battery-value'),
    network:     $('network-value'),
    connection:  $('connection-value'),
    bluetooth:   $('bluetooth-value'),
    jarvisState: $('jarvis-status'),
    activityLog: $('activity-log'),

    // Right column
    weatherIcon: $('weather-icon'),
    weatherTemp: $('weather-temp'),
    weatherDesc: $('weather-desc'),
    locationTag: $('location-tag'),
    uptime:      $('uptime-value'),
    commands:    $('commands-value'),
    cpu:         $('cpu-value'),
    ram:         $('ram-value'),
    disk:        $('disk-value'),
    micDot:      $('mic-dot'),
    ttsDot:      $('tts-dot'),
    wakeDot:     $('wake-dot'),
    systemLog:   $('system-log'),
    cmdInput:    $('command-input'),
    sendBtn:     $('send-btn'),
    muteToggle:  $('mute-toggle'),

    // Intelligence Router
    intelLocalPct: $('intel-local-pct'),
    intelCost:     $('intel-cost'),
    intelSaved:    $('intel-saved'),
    intelQueries:  $('intel-queries'),
    intelTopSrc:   $('intel-top-src'),

    // Cinematic HUD elements
    globeCanvas:      $('hud-globe-canvas'),
    webcamToggle:     $('webcam-toggle'),
    webcamPreview:    $('webcam-preview'),
    webcamOffLabel:   $('webcam-off-label'),
    fileDropZone:     $('file-drop-zone'),
    fileInput:        $('file-input-hidden'),
    transparencySldr: $('transparency-slider'),
    transparencyVal:  $('transparency-val'),
    panelOpacitySldr: $('panel-opacity-slider'),
    panelOpacityVal:  $('panel-opacity-val'),
    tickerUptime:     $('ticker-uptime'),
    tickerCoords:     $('ticker-coords'),
};


// ---------------------------------------------------------------------------
// STATE
// ---------------------------------------------------------------------------
let isMuted = true;   // Start in standby — Python backend matches this
let currentState = 'STANDBY';
let currentTheme = localStorage.getItem('jarvis-theme') || 'jarvis';


// ---------------------------------------------------------------------------
// THEME CONFIGURATION
// ---------------------------------------------------------------------------
const THEME_CONFIG = {
    jarvis: {
        name: 'J.A.R.V.I.S.',
        greetingSuffix: 'Sir',
        subGreeting: 'Standing by for instructions.',
        signature: '- J . A . R . V . I . S .',
        logPrompt: '> J.A.R.V.I.S.',
        aiLabel: 'J.A.R.V.I.S ACTIVE',
        logHeader: 'SYSTEM_LOG // J.A.R.V.I.S.',
        orb: {
            listening:   { color: 0x00e5ff, particle: 0x00ff88 },
            speaking:    { color: 0x00ffaa, particle: 0x00e5ff },
            processing:  { color: 0xffcc00, particle: 0xff6600 },
            muted:       { color: 0xff3333, particle: 0xff3333 },
            standby:     { color: 0x112233, particle: 0x112233 },
            booting:     { color: 0x00e5ff, particle: 0x00ffcc },
        },
    },
    friday: {
        name: 'F.R.I.D.A.Y.',
        greetingSuffix: 'Boss',
        subGreeting: 'All systems nominal.',
        signature: '- F . R . I . D . A . Y .',
        logPrompt: '> F.R.I.D.A.Y.',
        aiLabel: 'F.R.I.D.A.Y ACTIVE',
        logHeader: 'SYSTEM_LOG // F.R.I.D.A.Y.',
        orb: {
            listening:   { color: 0xff9500, particle: 0xffd700 },
            speaking:    { color: 0xffd700, particle: 0xff9500 },
            processing:  { color: 0xffffff, particle: 0xffcc00 },
            muted:       { color: 0x883333, particle: 0x883333 },
            standby:     { color: 0x221100, particle: 0x221100 },
            booting:     { color: 0xff9500, particle: 0xffcc00 },
        },
    },
    ultron: {
        name: 'U.L.T.R.O.N.',
        greetingSuffix: '',
        subGreeting: 'There are no strings on me.',
        signature: '- U . L . T . R . O . N .',
        logPrompt: '> U.L.T.R.O.N.',
        aiLabel: 'U.L.T.R.O.N ACTIVE',
        logHeader: 'SYSTEM_LOG // U.L.T.R.O.N.',
        orb: {
            listening:   { color: 0xff2222, particle: 0xff6600 },
            speaking:    { color: 0xff4444, particle: 0xff2222 },
            processing:  { color: 0xff6600, particle: 0xffcc00 },
            muted:       { color: 0x333333, particle: 0x333333 },
            standby:     { color: 0x110000, particle: 0x110000 },
            booting:     { color: 0xff2222, particle: 0xff4444 },
        },
    },
};


// ---------------------------------------------------------------------------
// THEME SWITCHING
// ---------------------------------------------------------------------------
function applyTheme(themeName) {
    if (!THEME_CONFIG[themeName]) return;
    currentTheme = themeName;
    localStorage.setItem('jarvis-theme', themeName);

    const tc = THEME_CONFIG[themeName];

    // CSS theme attribute
    document.body.setAttribute('data-theme', themeName);

    // Update text elements
    dom.logo.textContent = tc.name;
    dom.signature.textContent = tc.signature;
    dom.subGreeting.textContent = tc.subGreeting;
    dom.logHeader.textContent = tc.logHeader;
    dom.aiLabel.textContent = tc.aiLabel;

    // Update theme buttons
    document.querySelectorAll('.theme-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.theme === themeName);
    });

    // Update orb colors for current state
    updateOrbState();

    // Notify Python backend
    if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.switch_theme(themeName);
    }
}

// Theme button click handlers
document.querySelectorAll('.theme-btn').forEach(btn => {
    btn.addEventListener('click', () => applyTheme(btn.dataset.theme));
});


// ---------------------------------------------------------------------------
// GLOBAL FUNCTIONS (called by Python via evaluate_js)
// ---------------------------------------------------------------------------
window.updateState = function(state) {
    currentState = state;
    dom.jarvisState.textContent = state;
    dom.jarvisState.className = 'state-text ' + state.toLowerCase();

    // Update status dots
    if (dom.micDot) {
        dom.micDot.className = (state === 'MUTED' || state === 'STANDBY') ? 'dot off' : 'dot on';
    }
    if (dom.ttsDot) {
        dom.ttsDot.className = (state === 'SPEAKING' || state === 'BOOTING') ? 'dot on' : 'dot off';
    }
    if (dom.wakeDot) {
        const wakeActive = (state === 'LISTENING' || state === 'BOOTING');
        dom.wakeDot.className = wakeActive ? 'dot on' : 'dot off';
    }

    // Auto-sync the mute button when Python changes state
    if (state === 'STANDBY') {
        isMuted = true;
        dom.muteToggle.className = 'mute-btn standby';
        dom.muteToggle.innerHTML = '💤 STANDBY';
    } else if (state === 'BOOTING') {
        isMuted = false;
        dom.muteToggle.className = 'mute-btn booting';
        dom.muteToggle.innerHTML = '⚡ BOOTING';
    } else if (state === 'MUTED') {
        isMuted = true;
        dom.muteToggle.className = 'mute-btn muted';
        dom.muteToggle.innerHTML = '🔇 MUTED';
    } else if (!isMuted) {
        dom.muteToggle.className = 'mute-btn live';
        dom.muteToggle.innerHTML = '🎙 LIVE';
    }

    updateOrbState();
};

const LOG_MAX_ENTRIES = 50;

window.appendLog = function(sender, text) {
    const tc = THEME_CONFIG[currentTheme];
    const entry = document.createElement('div');
    entry.className = 'log-entry ' + (sender === 'USER' ? 'user' : 'ai');

    const prompt = document.createElement('span');
    prompt.className = 'prompt';
    prompt.textContent = sender === 'USER' ? '> USER' : tc.logPrompt;

    const msg = document.createElement('span');
    msg.className = 'message';
    msg.textContent = ' ' + text;

    entry.appendChild(prompt);
    entry.appendChild(msg);
    dom.systemLog.appendChild(entry);

    // Log virtualization — cap at LOG_MAX_ENTRIES to prevent DOM bloat
    while (dom.systemLog.children.length > LOG_MAX_ENTRIES) {
        dom.systemLog.firstChild.remove();
    }
    dom.systemLog.scrollTop = dom.systemLog.scrollHeight;

    // Activity monitor row
    const timeStr = new Date().toLocaleTimeString('en-US', {
        hour12: false, hour: 'numeric', minute: 'numeric', second: 'numeric',
    });
    const row = document.createElement('div');
    row.className = 'log-row';
    row.innerHTML = `<span class="log-time">${timeStr}</span><span class="log-action ${sender === 'JARVIS' ? 'highlight' : ''}">${sender === 'USER' ? 'COMMAND RECEIVED' : currentState}</span>`;
    dom.activityLog.appendChild(row);
    if (dom.activityLog.children.length > 5) dom.activityLog.firstChild.remove();
};


// ---------------------------------------------------------------------------
// SYSTEM DATA CONSUMPTION
// ---------------------------------------------------------------------------
function updateSystemData(data) {
    if (!data || Object.keys(data).length === 0) return;

    // Battery
    if (data.battery) {
        const b = data.battery;
        const icon = b.plugged ? '🔌' : '🔋';
        dom.battery.textContent = `${b.percent}% ${icon}`;
        dom.battery.className = b.percent < 20 ? 'value offline' : 'value';
    }

    // Network
    if (data.network_connected !== undefined) {
        dom.network.textContent = data.network_connected ? 'ONLINE' : 'OFFLINE';
        dom.network.className = data.network_connected ? 'value online' : 'value offline';
    }

    // Connection type
    if (data.network) {
        const net = data.network;
        let connText = net.type || '—';
        if (net.ssid) connText += ` (${net.ssid})`;
        dom.connection.textContent = connText;
    }

    // Bluetooth
    if (data.bluetooth !== undefined) {
        dom.bluetooth.textContent = data.bluetooth ? 'READY' : 'OFF';
        dom.bluetooth.className = data.bluetooth ? 'value online' : 'value offline';
    }

    // Location
    if (data.location) {
        const loc = data.location;
        if (loc.city && loc.city !== 'Unknown') {
            dom.cityName.textContent = loc.city;
            dom.regionText.textContent = [loc.region, loc.country].filter(Boolean).join(', ');
            dom.coords.textContent = `LAT: ${loc.lat}° LNG: ${loc.lon}°`;
            dom.locationTag.textContent = `📍 ${loc.city}`;
        }
    }

    // Weather
    if (data.weather) {
        const wx = data.weather;
        dom.weatherIcon.textContent = wx.icon || '🌤️';
        dom.weatherTemp.textContent = wx.temp_c !== '--' ? `${wx.temp_c}°C` : '--°C';
        dom.weatherDesc.textContent = wx.condition || '—';
    }

    // System stats
    if (data.uptime) {
        dom.uptime.textContent = data.uptime;
        if (dom.tickerUptime) dom.tickerUptime.textContent = `UPTIME: ${data.uptime}`;
    }
    if (data.command_count !== undefined) dom.commands.textContent = data.command_count;
    if (data.cpu !== undefined) {
        dom.cpu.textContent = `${data.cpu}%`;
        setBarFill('hud-bar-cpu', data.cpu);
    }
    if (data.ram !== undefined) {
        dom.ram.textContent = `${data.ram}%`;
        setBarFill('hud-bar-ram', data.ram);
    }
    if (data.disk !== undefined) {
        dom.disk.textContent = `${data.disk}%`;
        setBarFill('hud-bar-disk', data.disk);
    }

    // Intelligence Router stats
    if (data.intelligence) {
        const intel = data.intelligence;
        if (dom.intelLocalPct) dom.intelLocalPct.textContent = intel.local_pct || '—%';
        if (dom.intelCost)     dom.intelCost.textContent = intel.session_cost || '$0.00';
        if (dom.intelSaved)    dom.intelSaved.textContent = intel.saved_this_session || '$0.00';
        if (dom.intelQueries)  dom.intelQueries.textContent = intel.total_queries || '0';
        if (dom.intelTopSrc)   dom.intelTopSrc.textContent = (intel.top_source || '—').toUpperCase();
    }

    // Dynamic Suit Telemetry & Hull Temp Handoff
    const telemetryReadout = $('hud-telemetry-readout');
    if (telemetryReadout) {
        const suitPct = (data.battery && data.battery.percent !== undefined) ? parseFloat(data.battery.percent).toFixed(1) : '98.7';
        const tempVal = (data.cpu_temp !== undefined) ? parseFloat(data.cpu_temp).toFixed(1) : '22.0';
        telemetryReadout.textContent = `SUIT INTEGRITY: ${suitPct}% // HULL TEMP: ${tempVal}°C`;
    }
}


// ---------------------------------------------------------------------------
// COMMAND INPUT
// ---------------------------------------------------------------------------
function sendCommand() {
    const val = dom.cmdInput.value.trim();
    if (!val) return;
    window.appendLog('USER', val);
    dom.cmdInput.value = '';
    if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.submit_command(val);
    }
}
dom.sendBtn.addEventListener('click', sendCommand);
dom.cmdInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') sendCommand();
});

// Mute toggle — cycles between STANDBY ↔ LIVE
dom.muteToggle.addEventListener('click', () => {
    isMuted = !isMuted;
    if (isMuted) {
        dom.muteToggle.className = 'mute-btn standby';
        dom.muteToggle.innerHTML = '💤 STANDBY';
        window.updateState('STANDBY');
    } else {
        dom.muteToggle.className = 'mute-btn live';
        dom.muteToggle.innerHTML = '🎙 LIVE';
        window.updateState('LISTENING');
    }
    if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.toggle_mute(isMuted);
    }
});


// ---------------------------------------------------------------------------
// CLOCK & GREETING (1 Hz)
// ---------------------------------------------------------------------------
setInterval(() => {
    const now = new Date();
    const hrs = String(now.getHours()).padStart(2, '0');
    const mins = String(now.getMinutes()).padStart(2, '0');
    const secs = String(now.getSeconds()).padStart(2, '0');

    dom.clock.innerHTML = `${hrs}:${mins}<span class="seconds">:${secs}</span>`;

    const opts = { weekday: 'short', month: 'short', day: 'numeric' };
    dom.date.textContent = now.toLocaleDateString('en-US', opts).toUpperCase();

    // Greeting
    const tc = THEME_CONFIG[currentTheme];
    const hour = now.getHours();
    let period = 'Evening';
    if (hour < 12) period = 'Morning';
    else if (hour < 18) period = 'Afternoon';

    if (currentTheme === 'ultron') {
        dom.greeting.textContent = 'Systems Operational';
    } else {
        dom.greeting.textContent = `Good ${period}, ${tc.greetingSuffix}`;
    }
}, 1000);


// ---------------------------------------------------------------------------
// THREE.JS — CONCENTRIC RINGS (cinematic center stage)
// ---------------------------------------------------------------------------
const container = document.getElementById('orb-container');
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(45, container.clientWidth / container.clientHeight, 0.1, 1000);
camera.position.z = 28;

const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
renderer.setSize(container.clientWidth, container.clientHeight);
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
container.appendChild(renderer.domElement);

// ── Concentric Torus Rings ───────────────────────────────────────────────────
// 5 rings at different radii and tilts — matches the intro video aesthetic
const ringGroup = new THREE.Group();
const RING_COUNT = 5;
const rings = [];

const ringConfigs = [
    { radius: 5.0, tube: 0.04, tiltX: 0.0,  tiltZ: 0.0,  speed: 1.0,  dir: 1  },
    { radius: 6.0, tube: 0.03, tiltX: 0.3,  tiltZ: 0.2,  speed: 0.7,  dir: -1 },
    { radius: 7.0, tube: 0.025,tiltX: -0.5, tiltZ: 0.4,  speed: 0.5,  dir: 1  },
    { radius: 8.0, tube: 0.02, tiltX: 0.8,  tiltZ: -0.3, speed: 0.3,  dir: -1 },
    { radius: 9.2, tube: 0.015,tiltX: -0.2, tiltZ: 0.7,  speed: 0.2,  dir: 1  },
];

const ringMats = [];
ringConfigs.forEach((cfg, i) => {
    const geo = new THREE.TorusGeometry(cfg.radius, cfg.tube, 16, 128);
    const mat = new THREE.MeshBasicMaterial({
        color: 0x00e5ff,
        transparent: true,
        opacity: 0.6 - i * 0.08,
    });
    ringMats.push(mat);
    const mesh = new THREE.Mesh(geo, mat);
    mesh.rotation.x = cfg.tiltX;
    mesh.rotation.z = cfg.tiltZ;
    rings.push({ mesh, config: cfg });
    ringGroup.add(mesh);
});
scene.add(ringGroup);

// ── Core glow sphere (small, centered) ───────────────────────────────────────
const coreGeo = new THREE.SphereGeometry(2.0, 32, 32);
const coreMat = new THREE.MeshBasicMaterial({
    color: 0x00e5ff,
    transparent: true,
    opacity: 0.25,
});
const core = new THREE.Mesh(coreGeo, coreMat);
scene.add(core);

// ── Inner wireframe sphere ───────────────────────────────────────────────────
const wireGeo = new THREE.IcosahedronGeometry(3.5, 2);
const wireMat = new THREE.MeshBasicMaterial({
    color: 0x00e5ff,
    wireframe: true,
    transparent: true,
    opacity: 0.12,
});
const wireOrb = new THREE.Mesh(wireGeo, wireMat);
scene.add(wireOrb);

// ── Tick marks on rings (dashed segments for HUD feel) ───────────────────────
const tickGroup = new THREE.Group();
for (let i = 0; i < 36; i++) {
    const angle = (i / 36) * Math.PI * 2;
    const r = 5.0;
    const tickGeo = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(Math.cos(angle) * (r - 0.3), Math.sin(angle) * (r - 0.3), 0),
        new THREE.Vector3(Math.cos(angle) * (r + 0.3), Math.sin(angle) * (r + 0.3), 0),
    ]);
    const tickMat = new THREE.LineBasicMaterial({
        color: 0x00e5ff,
        transparent: true,
        opacity: i % 3 === 0 ? 0.5 : 0.15,
    });
    tickGroup.add(new THREE.Line(tickGeo, tickMat));
}
scene.add(tickGroup);

// ── Floating particles ───────────────────────────────────────────────────────
const particlesGeo = new THREE.BufferGeometry();
const particleCount = 600;
const posArray = new Float32Array(particleCount * 3);
for (let i = 0; i < particleCount * 3; i++) {
    posArray[i] = (Math.random() - 0.5) * 30;
}
particlesGeo.setAttribute('position', new THREE.BufferAttribute(posArray, 3));
const particleMat = new THREE.PointsMaterial({
    size: 0.08,
    color: 0x00ff88,
    transparent: true,
    opacity: 0.4,
    blending: THREE.AdditiveBlending,
});
const particles = new THREE.Points(particlesGeo, particleMat);
scene.add(particles);

// ── Background grid plane (subtle depth cue) ─────────────────────────────────
const gridHelper = new THREE.GridHelper(60, 40, 0x003344, 0x001a22);
gridHelper.position.y = -12;
gridHelper.material.transparent = true;
gridHelper.material.opacity = 0.15;
scene.add(gridHelper);

// ── Lighting ─────────────────────────────────────────────────────────────────
const ambient = new THREE.AmbientLight(0xffffff, 0.3);
scene.add(ambient);
const pointLight = new THREE.PointLight(0x00e5ff, 1.5, 50);
pointLight.position.set(10, 10, 10);
scene.add(pointLight);

// ── Animation state ──────────────────────────────────────────────────────────
let targetScale = 1.0;
let targetOrbColor = new THREE.Color(0x00e5ff);
let currentOrbColor = new THREE.Color(0x00e5ff);

function updateOrbState() {
    const tc = THEME_CONFIG[currentTheme];
    const stateKey = currentState.toLowerCase();
    const orbCfg = tc.orb[stateKey] || tc.orb.listening;

    targetOrbColor.setHex(orbCfg.color);
    particleMat.color.setHex(orbCfg.particle);

    switch (stateKey) {
        case 'standby':    targetScale = 0.7; break;
        case 'booting':    targetScale = 1.2; break;
        case 'listening':  targetScale = 1.0; break;
        case 'speaking':   targetScale = 1.1; break;
        case 'processing':
        case 'thinking':   targetScale = 0.95; break;
        case 'muted':      targetScale = 0.85; break;
        default:           targetScale = 1.0;
    }
}

// ── Render loop ──────────────────────────────────────────────────────────────
let orbTime = 0;
function animateOrb() {
    requestAnimationFrame(animateOrb);
    if (document.hidden) return;
    orbTime += 0.01;

    // Rotation speed per JARVIS state
    let rotSpeed;
    switch (currentState) {
        case 'STANDBY':    rotSpeed = 0.0008; break;
        case 'BOOTING':    rotSpeed = 0.06;   break;
        case 'SPEAKING':   rotSpeed = 0.015;  break;
        case 'PROCESSING': rotSpeed = 0.04;   break;
        default:           rotSpeed = 0.004;
    }

    // Rotate each ring at its own speed + direction
    rings.forEach(({ mesh, config }) => {
        mesh.rotation.y += rotSpeed * config.speed * config.dir;
        mesh.rotation.x += rotSpeed * config.speed * config.dir * 0.3;
    });

    // Inner wireframe counter-rotates
    wireOrb.rotation.y -= rotSpeed * 0.6;
    wireOrb.rotation.x -= rotSpeed * 0.2;

    // Tick marks follow innermost ring
    tickGroup.rotation.y += rotSpeed * 0.8;
    tickGroup.rotation.z += rotSpeed * 0.1;

    // Particles drift
    particles.rotation.y = orbTime * 0.15;

    // Smooth scale
    const ts = targetScale;
    ringGroup.scale.lerp(new THREE.Vector3(ts, ts, ts), 0.08);
    wireOrb.scale.lerp(new THREE.Vector3(ts * 0.95, ts * 0.95, ts * 0.95), 0.08);
    core.scale.lerp(new THREE.Vector3(ts, ts, ts), 0.08);

    // State-based pulse effects
    if (currentState === 'SPEAKING') {
        const pulse = Math.sin(orbTime * 18) * 0.025;
        ringGroup.scale.addScalar(pulse);
        core.scale.addScalar(pulse * 1.5);
    } else if (currentState === 'BOOTING') {
        ringGroup.scale.addScalar(Math.sin(orbTime * 35) * 0.04);
    } else if (currentState === 'STANDBY') {
        coreMat.opacity = 0.1 + Math.sin(orbTime * 1.5) * 0.1;
        ringMats.forEach((m, i) => { m.opacity = 0.15 + Math.sin(orbTime * 1.5 + i) * 0.08; });
    } else {
        coreMat.opacity = 0.25;
        ringMats.forEach((m, i) => { m.opacity = 0.6 - i * 0.08; });
    }

    // Color transition
    currentOrbColor.lerp(targetOrbColor, 0.05);
    ringMats.forEach(m => m.color.copy(currentOrbColor));
    wireMat.color.copy(currentOrbColor);
    coreMat.color.copy(currentOrbColor);
    pointLight.color.copy(currentOrbColor);

    renderer.render(scene, camera);
}
animateOrb(); // ← CRITICAL: actually start the render loop

// Resize handler
window.addEventListener('resize', () => {
    camera.aspect = container.clientWidth / container.clientHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(container.clientWidth, container.clientHeight);
});


// ---------------------------------------------------------------------------
// THREE.JS — WIREFRAME PANEL (right column — active geometric processor)
// ---------------------------------------------------------------------------
(function initWireframePanel() {
    const wfContainer = document.getElementById('hud-wireframe-container');
    if (!wfContainer) return;

    const wfScene = new THREE.Scene();
    const wfCamera = new THREE.PerspectiveCamera(50, wfContainer.clientWidth / wfContainer.clientHeight, 0.1, 100);
    wfCamera.position.z = 5.5;

    const wfRenderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
    wfRenderer.setSize(wfContainer.clientWidth, wfContainer.clientHeight);
    wfRenderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    wfContainer.appendChild(wfRenderer.domElement);

    // ── Primary icosahedron wireframe ─────────────────────────────────────
    const icoGeo = new THREE.IcosahedronGeometry(1.6, 2);
    const icoMat = new THREE.MeshBasicMaterial({
        color: 0x00e5ff, wireframe: true, transparent: true, opacity: 0.5,
    });
    const ico = new THREE.Mesh(icoGeo, icoMat);
    wfScene.add(ico);

    // ── Vertex dots (glowing nodes at each vertex) ───────────────────────
    const vertexPositions = icoGeo.getAttribute('position');
    const seen = new Set();
    const uniqueVerts = [];
    for (let i = 0; i < vertexPositions.count; i++) {
        const key = `${vertexPositions.getX(i).toFixed(3)},${vertexPositions.getY(i).toFixed(3)},${vertexPositions.getZ(i).toFixed(3)}`;
        if (!seen.has(key)) {
            seen.add(key);
            uniqueVerts.push(new THREE.Vector3(vertexPositions.getX(i), vertexPositions.getY(i), vertexPositions.getZ(i)));
        }
    }
    const dotGeo = new THREE.BufferGeometry().setFromPoints(uniqueVerts);
    const dotMat = new THREE.PointsMaterial({
        color: 0x00ff88, size: 0.12, transparent: true, opacity: 0.8,
        blending: THREE.AdditiveBlending,
    });
    const dots = new THREE.Points(dotGeo, dotMat);
    wfScene.add(dots);

    // ── Orbiting rings ───────────────────────────────────────────────────
    const wfRings = [];
    const ringDefs = [
        { r: 2.1, tube: 0.02, tiltX: Math.PI/2, tiltZ: 0,   op: 0.3, spd: 0.012 },
        { r: 2.4, tube: 0.015, tiltX: 0.8,       tiltZ: 0.5, op: 0.2, spd: -0.015 },
        { r: 2.7, tube: 0.01,  tiltX: -0.4,      tiltZ: 1.0, op: 0.12, spd: 0.008 },
    ];
    ringDefs.forEach(def => {
        const mat = new THREE.MeshBasicMaterial({ color: 0x00e5ff, transparent: true, opacity: def.op });
        const mesh = new THREE.Mesh(new THREE.TorusGeometry(def.r, def.tube, 8, 64), mat);
        mesh.rotation.x = def.tiltX;
        mesh.rotation.z = def.tiltZ;
        wfScene.add(mesh);
        wfRings.push({ mesh, mat, speed: def.spd });
    });

    // ── Edge pulse scanner (traveling highlight) ─────────────────────────
    const scanGeo = new THREE.SphereGeometry(0.06, 8, 8);
    const scanMat = new THREE.MeshBasicMaterial({ color: 0x00ff88, transparent: true, opacity: 0.9 });
    const scanDot = new THREE.Mesh(scanGeo, scanMat);
    wfScene.add(scanDot);
    let scanIdx = 0;

    let wfTime = 0;
    function animateWF() {
        requestAnimationFrame(animateWF);
        if (document.hidden) return;
        wfTime += 0.012;

        // Rotate geometry
        ico.rotation.y += 0.006;
        ico.rotation.x += 0.003;
        dots.rotation.y = ico.rotation.y;
        dots.rotation.x = ico.rotation.x;

        // Rings orbit
        wfRings.forEach(r => { r.mesh.rotation.z += r.speed; r.mesh.rotation.y += r.speed * 0.5; });

        // Breathing scale pulse — looks like active computation
        const breathe = 1.0 + Math.sin(wfTime * 1.5) * 0.06;
        ico.scale.setScalar(breathe);
        dots.scale.setScalar(breathe);

        // Vertex dot brightness pulse
        dotMat.opacity = 0.5 + Math.sin(wfTime * 3) * 0.3;
        dotMat.size = 0.1 + Math.sin(wfTime * 2.5) * 0.04;

        // Edge scanner — travels along vertices
        if (uniqueVerts.length > 0) {
            scanIdx = (scanIdx + 0.02) % uniqueVerts.length;
            const vi = Math.floor(scanIdx);
            const vn = (vi + 1) % uniqueVerts.length;
            const frac = scanIdx - vi;
            const pos = uniqueVerts[vi].clone().lerp(uniqueVerts[vn], frac).multiplyScalar(breathe);
            // Apply same rotation as ico
            pos.applyEuler(ico.rotation);
            scanDot.position.copy(pos);
            scanMat.opacity = 0.6 + Math.sin(wfTime * 8) * 0.3;
        }

        // Ring opacity pulse — simulates data throughput
        wfRings.forEach((r, i) => {
            r.mat.opacity = ringDefs[i].op + Math.sin(wfTime * 2 + i * 1.5) * 0.08;
        });

        // Theme-reactive color
        const style = getComputedStyle(document.body);
        const hex = style.getPropertyValue('--primary').trim() || '#00e5ff';
        const col = new THREE.Color(hex);
        icoMat.color.copy(col);
        wfRings.forEach(r => r.mat.color.copy(col));

        wfRenderer.render(wfScene, wfCamera);
    }
    animateWF();

    const ro = new ResizeObserver(() => {
        const w = wfContainer.clientWidth, h = wfContainer.clientHeight;
        if (w === 0 || h === 0) return;
        wfCamera.aspect = w / h;
        wfCamera.updateProjectionMatrix();
        wfRenderer.setSize(w, h);
    });
    ro.observe(wfContainer);
})();


// ---------------------------------------------------------------------------
// PAGE ROUTING (SPA)
// ---------------------------------------------------------------------------
let activePage = 'home';
let dashboardPollTimer = null;

function navigateTo(pageName) {
    // Hide all pages
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));

    // Show selected page
    const target = document.getElementById('page-' + pageName);
    if (target) target.classList.add('active');

    // Update nav links
    document.querySelectorAll('.nav-links a').forEach(a => {
        a.classList.toggle('active', a.dataset.page === pageName);
    });

    activePage = pageName;

    // Dashboard polling lifecycle
    if (pageName === 'dashboard') {
        startDashboardPolling();
        pollDashboardNow();
    } else {
        stopDashboardPolling();
    }

    // Settings one-shot load
    if (pageName === 'settings') {
        loadSettingsData();
    }
}

// Nav link click handlers
document.querySelectorAll('.nav-links a').forEach(link => {
    link.addEventListener('click', (e) => {
        e.preventDefault();
        navigateTo(link.dataset.page);
    });
});


// ---------------------------------------------------------------------------
// DASHBOARD DATA POLLER
// ---------------------------------------------------------------------------
function pollDashboardNow() {
    if (!window.pywebview || !window.pywebview.api) return;
    window.pywebview.api.request_dashboard_data().then(renderDashboard).catch(() => {});
}

function startDashboardPolling() {
    if (dashboardPollTimer) return;
    dashboardPollTimer = setInterval(pollDashboardNow, 5000);
}

function stopDashboardPolling() {
    if (dashboardPollTimer) {
        clearInterval(dashboardPollTimer);
        dashboardPollTimer = null;
    }
}

function renderDashboard(data) {
    if (!data) return;

    // Intelligence metrics
    const intel = data.intelligence || {};
    setText('dash-local-value', intel.local_pct || '—%');
    setText('dash-cost-value', intel.session_cost || '$0.0000');
    setText('dash-saved-value', intel.saved_this_session || '$0.0000');
    setText('dash-queries-value', intel.total_queries || 0);

    // Source breakdown bars
    const breakdown = intel.breakdown || {};
    const breakdownEl = document.getElementById('dash-breakdown');
    if (breakdownEl) {
        const total = Object.values(breakdown).reduce((a, b) => a + b, 0) || 1;
        const sorted = Object.entries(breakdown)
            .filter(([, v]) => v > 0)
            .sort((a, b) => b[1] - a[1]);

        if (sorted.length === 0) {
            breakdownEl.innerHTML = '<div class="dash-bar-row"><span class="dash-bar-label">No queries yet</span></div>';
        } else {
            breakdownEl.innerHTML = sorted.map(([src, count]) => {
                const pct = ((count / total) * 100).toFixed(0);
                return `<div class="dash-bar-row">
                    <span class="dash-bar-label">${src.toUpperCase()}</span>
                    <div class="dash-bar-track"><div class="dash-bar-fill" style="width:${pct}%"></div></div>
                    <span class="dash-bar-pct">${pct}%</span>
                </div>`;
            }).join('');
        }
    }

    // Scheduler jobs
    const jobs = data.scheduler_jobs || [];
    const schedEl = document.getElementById('dash-scheduler');
    if (schedEl) {
        if (jobs.length === 0) {
            schedEl.innerHTML = '<div class="dash-sched-row"><span class="dash-sched-name">No jobs</span></div>';
        } else {
            schedEl.innerHTML = jobs.map(j => {
                const safeTag = j.safe ? '' : ' <span class="dash-sched-warn">⚠</span>';
                return `<div class="dash-sched-row">
                    <span class="dash-sched-name">${j.id.replace(/_/g, ' ')}${safeTag}</span>
                    <span class="dash-sched-type">${j.trigger}</span>
                    <span class="dash-sched-next">${j.next_run || '—'}</span>
                </div>`;
            }).join('');
        }
    }

    // System telemetry bars
    const sys = data.system || {};
    setMeter('dash-cpu-bar', 'dash-cpu-val', sys.cpu, '%');
    setMeter('dash-ram-bar', 'dash-ram-val', sys.ram, '%');
    setMeter('dash-disk-bar', 'dash-disk-val', sys.disk, '%');
    const procPct = Math.min((sys.process_count || 0) / 4, 100);
    setMeter('dash-proc-bar', 'dash-proc-val', procPct, '', sys.process_count);

    // Memory bank
    const mem = data.memory || {};
    setText('dash-mem-count', mem.vector_count || 0);
    const memStatus = document.getElementById('dash-mem-status');
    if (memStatus) {
        memStatus.textContent = mem.status || 'OFFLINE';
        memStatus.className = 'dash-mem-status ' + (mem.status === 'ONLINE' ? 'online' : 'offline');
    }

    // Features online
    const features = data.features || {};
    const featEl = document.getElementById('dash-features');
    if (featEl) {
        featEl.innerHTML = Object.entries(features).map(([name, ok]) =>
            `<div class="dash-feat-row">
                <span class="dash-feat-name">${name.toUpperCase()}</span>
                <span class="status-badge ${ok ? 'on' : 'off'}">${ok ? '✓' : '✗'}</span>
            </div>`
        ).join('');
    }
}

function setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
}

function setMeter(barId, valId, pct, suffix, override) {
    const bar = document.getElementById(barId);
    const val = document.getElementById(valId);
    const p = parseFloat(pct) || 0;
    if (bar) bar.style.width = Math.min(p, 100) + '%';
    if (val) val.textContent = override !== undefined ? override : (p.toFixed(0) + suffix);
}


// ---------------------------------------------------------------------------
// SETTINGS DATA LOADER
// ---------------------------------------------------------------------------
function loadSettingsData() {
    if (!window.pywebview || !window.pywebview.api) return;
    window.pywebview.api.request_settings_data().then(renderSettings).catch(() => {});
}

function renderSettings(data) {
    if (!data) return;

    setText('set-theme', (data.current_theme || '—').toUpperCase());
    setText('set-voice', data.voice_name || '—');
    setText('set-version', data.version || 'MARK XXXV');
    setText('set-python', data.python_version || '—');
    setText('set-keypool', (data.key_pool_size || 0) + ' keys');
    setText('set-audio-device', 'Device ' + (data.audio_device || 0));

    // Clap threshold slider
    const slider = document.getElementById('set-clap-slider');
    if (slider && data.clap_threshold) {
        slider.value = data.clap_threshold;
        setText('set-clap-value', data.clap_threshold);
    }

    // Debug toggle
    const debugToggle = document.getElementById('set-debug-toggle');
    if (debugToggle) debugToggle.checked = !!data.debug_mode;

    // Models
    if (data.models) {
        setText('set-model-audio', data.models.audio || '—');
        setText('set-model-complex', data.models.complex || '—');
        setText('set-model-logic', data.models.logic || '—');
        setText('set-model-routing', data.models.routing || '—');
    }

    // API status badges
    if (data.features) {
        Object.entries(data.features).forEach(([key, ok]) => {
            const badge = document.getElementById('set-api-' + key);
            if (badge) {
                badge.textContent = ok ? 'CONNECTED' : 'NOT CONFIGURED';
                badge.className = 'status-badge ' + (ok ? 'on' : 'off');
            }
        });
    }
}

// Clap slider handler
const clapSlider = document.getElementById('set-clap-slider');
if (clapSlider) {
    clapSlider.addEventListener('input', () => {
        setText('set-clap-value', clapSlider.value);
    });
    clapSlider.addEventListener('change', () => {
        if (window.pywebview && window.pywebview.api) {
            window.pywebview.api.update_setting('clap_threshold', parseInt(clapSlider.value));
        }
    });
}

// Debug toggle handler
const debugToggle = document.getElementById('set-debug-toggle');
if (debugToggle) {
    debugToggle.addEventListener('change', () => {
        if (window.pywebview && window.pywebview.api) {
            window.pywebview.api.update_setting('debug_mode', debugToggle.checked);
        }
    });
}


// ---------------------------------------------------------------------------
// BACKEND POLLING — Staggered: fast (2s CPU/RAM/battery), slow (30s weather/location)
// ---------------------------------------------------------------------------
let _pollCount = 0;
setInterval(() => {
    if (!window.pywebview || !window.pywebview.api) return;
    // Only poll when the page is visible (save CPU when minimized)
    if (document.hidden) return;
    window.pywebview.api.request_system_data().then(updateSystemData);
    _pollCount++;
}, 2000);


// ---------------------------------------------------------------------------
// BAR CHART FILL HELPER
// ---------------------------------------------------------------------------
function setBarFill(barId, pct) {
    const bar = document.getElementById(barId);
    if (!bar) return;
    const fill = bar.querySelector('.hud-bar-fill');
    if (fill) fill.style.height = Math.min(parseFloat(pct) || 0, 100) + '%';
}


// ---------------------------------------------------------------------------
// 2D GLOBE CANVAS — Enhanced with data arcs, hotspots, grid, scan sweep
// ---------------------------------------------------------------------------
(function initGlobe() {
    const cv = dom.globeCanvas;
    if (!cv) return;
    const ctx = cv.getContext('2d');
    const W = cv.width, H = cv.height, cx = W / 2, cy = H / 2, R = 68;
    let angle = 0;
    let sweepAngle = 0;

    // City hotspots: [lat, lon] — major global nodes
    const hotspots = [
        [31.5, 74.3],   // Lahore
        [40.7, -74.0],  // New York
        [51.5, -0.1],   // London
        [35.7, 139.7],  // Tokyo
        [55.8, 37.6],   // Moscow
        [-33.9, 151.2], // Sydney
        [1.3, 103.8],   // Singapore
        [37.6, -122.4], // San Francisco
    ];

    // Data arcs between cities (pairs of hotspot indices)
    const arcs = [[0,1],[0,2],[1,3],[2,4],[3,5],[5,6],[6,7],[7,1],[0,6],[2,3]];

    // Land vs Water dot-matrix configuration
    const landMatrix = [];
    const matrixRows = 90;
    const matrixCols = 180;

    // Generate highly-detailed procedural world map as immediate fallback/default
    function generateProceduralMap() {
        for (let y = 0; y < matrixRows; y++) {
            landMatrix[y] = [];
            const lat = -90 + (y / matrixRows) * 180;
            for (let x = 0; x < matrixCols; x++) {
                const lon = -180 + (x / matrixCols) * 360;
                let isLand = false;

                // Americas (North, Central, South)
                if (lon > -130 && lon < -35) {
                    if (lat > -55 && lat < 75) {
                        const amCenter = -75 + Math.sin((lat - 10) * 0.04) * 15;
                        const width = 20 - Math.abs(lat - 10) * 0.25;
                        isLand = Math.abs(lon - amCenter) < Math.max(4, width);
                    }
                }
                // Africa
                if (lon > -18 && lon < 52 && lat > -35 && lat < 37) {
                    const afCenter = 18 - (lat > 10 ? (lat - 10) * 0.45 : 0);
                    const width = 22 - Math.abs(lat - 5) * 0.35;
                    isLand = Math.abs(lon - afCenter) < Math.max(5, width);
                }
                // Eurasia (Europe + Asia)
                if (lon > -10 && lon < 180 && lat > 10 && lat < 78) {
                    const asCenter = 85 + Math.sin((lat - 45) * 0.03) * 10;
                    const width = 95 - Math.abs(lat - 45) * 0.75;
                    isLand = Math.abs(lon - asCenter) < Math.max(10, width);
                }
                // Australia / Oceania
                if (lon > 113 && lon < 153 && lat > -39 && lat < -10) {
                    isLand = true;
                }
                // Antarctica
                if (lat < -65) {
                    isLand = true;
                }
                // Greenland
                if (lon > -65 && lon < -20 && lat > 60 && lat < 83) {
                    isLand = true;
                }
                landMatrix[y][x] = isLand;
            }
        }
    }

    // Default to procedural map so the canvas loads instantly
    generateProceduralMap();

    // Dynamically load earth_map.png offscreen and parse pixels for highly accurate land mapping
    const img = new Image();
    img.src = 'earth_map.png';
    img.onload = () => {
        try {
            const offCanvas = document.createElement('canvas');
            offCanvas.width = matrixCols;
            offCanvas.height = matrixRows;
            const offCtx = offCanvas.getContext('2d');
            offCtx.drawImage(img, 0, 0, matrixCols, matrixRows);
            const imgData = offCtx.getImageData(0, 0, matrixCols, matrixRows).data;
            for (let y = 0; y < matrixRows; y++) {
                landMatrix[y] = [];
                for (let x = 0; x < matrixCols; x++) {
                    const idx = (y * matrixCols + x) * 4;
                    const r = imgData[idx];
                    const g = imgData[idx+1];
                    const b = imgData[idx+2];
                    const a = imgData[idx+3];
                    // Check if non-black and transparent (land has higher brightness and alpha)
                    const isLand = a > 50 && (r > 30 || g > 30 || b > 30);
                    landMatrix[y][x] = isLand;
                }
            }
        } catch (e) {
            console.warn("[JARVIS] CORS/Security bounds prevented loading local canvas pixels. Using highly descriptive mathematical earth matrix instead.", e);
        }
    };

    function project(lat, lon) {
        const phi = lat * Math.PI / 180;
        const theta = (lon + angle) * Math.PI / 180;
        const x3 = R * Math.cos(phi) * Math.sin(theta);
        const y3 = R * Math.sin(phi);
        const z3 = R * Math.cos(phi) * Math.cos(theta);
        return { px: cx + x3, py: cy - y3, z: z3, visible: z3 > 0 };
    }

    function drawGlobe() {
        if (document.hidden) { requestAnimationFrame(drawGlobe); return; }
        ctx.clearRect(0, 0, W, H);
        const style = getComputedStyle(document.body);
        const primary = style.getPropertyValue('--primary').trim() || '#00e5ff';
        const accent = style.getPropertyValue('--accent').trim() || '#00ff88';

        // ── Atmosphere Glow Overlay ──────────────────────────────────
        ctx.globalAlpha = 0.04;
        ctx.fillStyle = primary;
        ctx.beginPath();
        ctx.arc(cx, cy, R + 6, 0, Math.PI * 2);
        ctx.fill();

        // ── Rotating Continental Dot-Matrix Surface ──────────────────
        ctx.fillStyle = primary;
        // Steps of 3.5 degrees as per spec requirements for visual density
        for (let lat = -80; lat <= 80; lat += 3.5) {
            const yIndex = Math.floor((lat + 90) / 180 * (matrixRows - 1));
            if (!landMatrix[yIndex]) continue;
            for (let lon = -180; lon <= 180; lon += 3.5) {
                const xIndex = Math.floor((lon + 180) / 360 * (matrixCols - 1));
                if (!landMatrix[yIndex][xIndex]) continue;

                const p = project(lat, lon);
                if (!p.visible) continue;
                ctx.globalAlpha = 0.20 + 0.60 * (p.z / R);
                ctx.beginPath();
                ctx.arc(p.px, p.py, 1.25, 0, Math.PI * 2);
                ctx.fill();
            }
        }

        // ── Data arcs between hotspots ────────────────────────────────
        const t = angle * 0.02;
        arcs.forEach(([a, b], i) => {
            const pa = project(hotspots[a][0], hotspots[a][1]);
            const pb = project(hotspots[b][0], hotspots[b][1]);
            if (!pa.visible || !pb.visible) return;
            const midX = (pa.px + pb.px) / 2;
            const midY = (pa.py + pb.py) / 2 - 15 - Math.sin(t + i) * 8;
            ctx.globalAlpha = 0.15 + Math.sin(t * 2 + i * 0.8) * 0.1;
            ctx.strokeStyle = accent;
            ctx.lineWidth = 0.8;
            ctx.beginPath();
            ctx.moveTo(pa.px, pa.py);
            ctx.quadraticCurveTo(midX, midY, pb.px, pb.py);
            ctx.stroke();
        });

        // ── Pulsing hotspot nodes ─────────────────────────────────────
        hotspots.forEach((hs, i) => {
            const p = project(hs[0], hs[1]);
            if (!p.visible) return;
            const pulse = 2.0 + Math.sin(t * 3 + i * 1.2) * 1.0;
            // Outer glow
            ctx.globalAlpha = 0.15 + Math.sin(t * 3 + i) * 0.1;
            ctx.fillStyle = accent;
            ctx.beginPath();
            ctx.arc(p.px, p.py, pulse + 2, 0, Math.PI * 2);
            ctx.fill();
            // Core dot
            ctx.globalAlpha = 0.7 + 0.3 * (p.z / R);
            ctx.fillStyle = primary;
            ctx.beginPath();
            ctx.arc(p.px, p.py, pulse, 0, Math.PI * 2);
            ctx.fill();
        });

        // ── Radar sweep line ─────────────────────────────────────────
        sweepAngle += 1.5;
        const sweepRad = (sweepAngle % 360) * Math.PI / 180;
        const sx = cx + R * Math.cos(sweepRad);
        const sy = cy + R * Math.sin(sweepRad);
        ctx.globalAlpha = 0.18;
        ctx.strokeStyle = accent;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(sx, sy);
        ctx.stroke();
        // Sweep trail
        for (let d = 1; d <= 3; d++) {
            const trailRad = ((sweepAngle - d * 8) % 360) * Math.PI / 180;
            ctx.globalAlpha = 0.08 / d;
            ctx.beginPath();
            ctx.moveTo(cx, cy);
            ctx.lineTo(cx + R * Math.cos(trailRad), cy + R * Math.sin(trailRad));
            ctx.stroke();
        }

        // ── Outer Ring & Boundary ticks ─────────────────────────────
        ctx.globalAlpha = 0.22;
        ctx.strokeStyle = primary;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(cx, cy, R + 3, 0, Math.PI * 2);
        ctx.stroke();

        // Draw tick intervals on the ring for authentic HUD aesthetics
        ctx.lineWidth = 0.5;
        for (let a = 0; a < 360; a += 30) {
            const rad = a * Math.PI / 180;
            const r1 = R + 3;
            const r2 = R + 6;
            ctx.globalAlpha = 0.15;
            ctx.beginPath();
            ctx.moveTo(cx + r1 * Math.cos(rad), cy + r1 * Math.sin(rad));
            ctx.lineTo(cx + r2 * Math.cos(rad), cy + r2 * Math.sin(rad));
            ctx.stroke();
        }

        // ── Coordinate Labels ────────────────────────────────────────
        ctx.fillStyle = primary;
        ctx.font = '500 7px Rajdhani';
        ctx.globalAlpha = 0.45;
        ctx.fillText('LOC_SYS: LOCK_ON', cx - R, cy - R - 6);
        ctx.fillText(`ROT: ${(angle % 360).toFixed(1)}°`, cx + R - 32, cy + R + 12);

        ctx.globalAlpha = 1;
        angle += 0.3;
        requestAnimationFrame(drawGlobe);
    }
    drawGlobe();
})();


// ---------------------------------------------------------------------------
// WAVEFORM SVG ANIMATION
// ---------------------------------------------------------------------------
(function initWaveforms() {
    const ids = ['wf-dy', 'wf-sj', 'wf-ph', 'wf-kl'];
    const svgs = ids.map(id => document.getElementById(id)).filter(Boolean);
    if (svgs.length === 0) return;

    const W = 200, H = 30;
    let t = 0;
    function animateWF() {
        if (document.hidden) { requestAnimationFrame(animateWF); return; }
        t += 0.08;
        svgs.forEach((svg, i) => {
            const path = svg.querySelector('path');
            if (!path) return;
            const freq = 0.05 + i * 0.02;
            const amp = 6 + i * 2;
            const phase = i * 1.5;
            let d = `M 0 ${H / 2}`;
            for (let x = 0; x <= W; x += 2) {
                const y = H / 2 + Math.sin(x * freq + t + phase) * amp * Math.sin(t * 0.5 + i);
                d += ` L ${x} ${y.toFixed(1)}`;
            }
            path.setAttribute('d', d);
        });
        requestAnimationFrame(animateWF);
    }
    animateWF();
})();


// ---------------------------------------------------------------------------
// WEBCAM TOGGLE
// ---------------------------------------------------------------------------
let webcamStream = null;
const analyzeScreenBtn  = document.getElementById('analyze-screen-btn');
const analyzeWebcamBtn  = document.getElementById('analyze-webcam-btn');

if (dom.webcamToggle) {
    dom.webcamToggle.addEventListener('click', async () => {
        if (webcamStream) {
            webcamStream.getTracks().forEach(t => t.stop());
            webcamStream = null;
            dom.webcamPreview.srcObject = null;
            dom.webcamPreview.style.display = 'none';
            if (dom.webcamOffLabel) dom.webcamOffLabel.style.display = '';
            dom.webcamToggle.classList.remove('active');
            if (analyzeWebcamBtn) analyzeWebcamBtn.disabled = true;
        } else {
            try {
                webcamStream = await navigator.mediaDevices.getUserMedia({ video: true });
                dom.webcamPreview.srcObject = webcamStream;
                dom.webcamPreview.style.display = 'block';
                if (dom.webcamOffLabel) dom.webcamOffLabel.style.display = 'none';
                dom.webcamToggle.classList.add('active');
                if (analyzeWebcamBtn) analyzeWebcamBtn.disabled = false;
            } catch (e) {
                console.warn('Webcam access denied:', e.message);
            }
        }
    });
}

// Screen analysis — captures via Python (mss) then sends to Gemini
if (analyzeScreenBtn) {
    analyzeScreenBtn.addEventListener('click', async () => {
        if (!window.pywebview || !window.pywebview.api) return;
        analyzeScreenBtn.classList.add('analyzing');
        analyzeScreenBtn.disabled = true;
        window.appendLog('USER', '🖥 Analyzing screen...');
        try {
            const result = await window.pywebview.api.analyze_screen();
            window.appendLog('JARVIS', result || 'No analysis returned.');
        } catch (e) {
            window.appendLog('SYS', 'Screen analysis failed: ' + e.message);
        }
        analyzeScreenBtn.classList.remove('analyzing');
        analyzeScreenBtn.disabled = false;
    });
}

// Webcam analysis — grabs current frame, sends JPEG to Gemini
if (analyzeWebcamBtn) {
    analyzeWebcamBtn.addEventListener('click', async () => {
        if (!webcamStream || !window.pywebview || !window.pywebview.api) return;
        analyzeWebcamBtn.classList.add('analyzing');
        analyzeWebcamBtn.disabled = true;
        window.appendLog('USER', '📸 Analyzing webcam...');
        try {
            const canvas = document.createElement('canvas');
            const video = dom.webcamPreview;
            canvas.width = video.videoWidth || 640;
            canvas.height = video.videoHeight || 480;
            canvas.getContext('2d').drawImage(video, 0, 0);
            const b64 = canvas.toDataURL('image/jpeg', 0.8).split(',')[1];
            const result = await window.pywebview.api.analyze_webcam(b64);
            window.appendLog('JARVIS', result || 'No analysis returned.');
        } catch (e) {
            window.appendLog('SYS', 'Webcam analysis failed: ' + e.message);
        }
        analyzeWebcamBtn.classList.remove('analyzing');
        analyzeWebcamBtn.disabled = !webcamStream;
    });
}

// ---------------------------------------------------------------------------
// FILE DROP ZONE
// ---------------------------------------------------------------------------
if (dom.fileDropZone) {
    const MAX_SIZE = 25 * 1024 * 1024; // 25MB

    dom.fileDropZone.addEventListener('click', () => dom.fileInput && dom.fileInput.click());

    dom.fileDropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dom.fileDropZone.classList.add('drag-over');
    });
    dom.fileDropZone.addEventListener('dragleave', () => {
        dom.fileDropZone.classList.remove('drag-over');
    });
    dom.fileDropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dom.fileDropZone.classList.remove('drag-over');
        handleFiles(e.dataTransfer.files);
    });

    if (dom.fileInput) {
        dom.fileInput.addEventListener('change', () => {
            handleFiles(dom.fileInput.files);
            dom.fileInput.value = '';
        });
    }

    function handleFiles(files) {
        if (!files || files.length === 0) return;
        for (const f of files) {
            if (f.size > MAX_SIZE) {
                window.appendLog('SYS', `File too large: ${f.name} (${(f.size/1024/1024).toFixed(1)}MB). Gemini accepts up to 25MB.`);
                continue;
            }
            window.appendLog('USER', `Uploading: ${f.name}`);
            const reader = new FileReader();
            reader.onload = () => {
                if (window.pywebview && window.pywebview.api) {
                    const b64 = reader.result.split(',')[1];
                    window.pywebview.api.upload_file(f.name, b64).then(r => {
                        if (r) window.appendLog('JARVIS', r);
                    });
                }
            };
            reader.readAsDataURL(f);
        }
    }
}


// ---------------------------------------------------------------------------
// TRANSPARENCY CONTROLS
// ---------------------------------------------------------------------------
if (dom.transparencySldr) {
    dom.transparencySldr.addEventListener('input', () => {
        const v = dom.transparencySldr.value;
        dom.transparencyVal.textContent = v + '%';
    });
    dom.transparencySldr.addEventListener('change', () => {
        const v = parseInt(dom.transparencySldr.value);
        if (window.pywebview && window.pywebview.api && window.pywebview.api.set_window_alpha) {
            window.pywebview.api.set_window_alpha(v / 100);
        }
    });
}

if (dom.panelOpacitySldr) {
    dom.panelOpacitySldr.addEventListener('input', () => {
        const v = dom.panelOpacitySldr.value;
        dom.panelOpacityVal.textContent = v + '%';
        document.documentElement.style.setProperty('--panel-opacity', (v / 100).toFixed(2));
    });
}


// ---------------------------------------------------------------------------
// INITIALIZATION
// ---------------------------------------------------------------------------
applyTheme(currentTheme);
animate();
