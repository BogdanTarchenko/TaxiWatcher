const STATUS_LABELS = {
  best: "Лучшая цена",
  acceptable: "Приемлемая цена",
  normal: "Обычная цена",
  not_enough_data: "Мало данных пока",
};

const ERROR_LABELS = {
  rate_limited: "Яндекс временно ограничил запросы",
  scrape_failed: "Не удалось получить цену",
};

function isStandalone() {
  return window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
}

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; i++) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

function renderDirection(key, info) {
  const card = document.querySelector(`.card[data-direction="${key}"]`);
  card.classList.remove("status-best", "status-acceptable", "status-normal", "status-error");

  const priceEl = card.querySelector(".price");
  const metaEl = card.querySelector(".meta");
  const bestList = card.querySelector(".best-today");

  if (info.error) {
    card.classList.add("status-error");
    priceEl.textContent = "—";
    metaEl.textContent = ERROR_LABELS[info.error] || info.error;
    return;
  }

  card.classList.add(`status-${info.status}`);
  priceEl.textContent = `${Math.round(info.price)} ₽`;

  const asOf = new Date(info.as_of);
  const timeStr = asOf.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  let metaText = `${STATUS_LABELS[info.status] || info.status} · на ${timeStr}`;
  if (info.bucket_median) {
    metaText += ` · обычно ~${Math.round(info.bucket_median)} ₽`;
  }
  metaEl.textContent = metaText;

  bestList.innerHTML = "";
}

function renderBestToday(key, rows) {
  const card = document.querySelector(`.card[data-direction="${key}"]`);
  const bestList = card.querySelector(".best-today");
  bestList.innerHTML = "";
  if (!rows || !rows.length) return;

  const cheapest = rows.reduce((a, b) => (b.median < a.median ? b : a));
  for (const row of rows) {
    const li = document.createElement("li");
    li.textContent = `${String(row.hour).padStart(2, "0")}:00 ~${Math.round(row.median)}₽`;
    if (row.hour === cheapest.hour) li.style.fontWeight = "700";
    bestList.appendChild(li);
  }
}

async function loadStatus() {
  const res = await fetch("api/status");
  const data = await res.json();

  for (const [key, info] of Object.entries(data.directions)) {
    renderDirection(key, info);
  }
  for (const [key, rows] of Object.entries(data.best_today)) {
    renderBestToday(key, rows);
  }

  const banner = document.getElementById("paused-banner");
  if (data.paused_until) {
    const until = new Date(data.paused_until).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
    banner.textContent = `Опрос цены на паузе (перегрузка) примерно до ${until}`;
    banner.classList.remove("hidden");
  } else {
    banner.classList.add("hidden");
  }

  const footer = document.getElementById("settings-footer");
  footer.textContent = `${data.settings.tariff} · окно ${data.settings.window} · ${data.settings.timezone}`;
}

function setSubscribedUi() {
  const btn = document.getElementById("subscribe-btn");
  btn.textContent = "Уведомления включены ✓";
  btn.disabled = true;
}

async function checkPushStatus() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return;

  try {
    const registration = await navigator.serviceWorker.getRegistration();
    if (!registration) return;
    const subscription = await registration.pushManager.getSubscription();
    if (subscription) setSubscribedUi();
  } catch (err) {
    console.warn("Не удалось проверить статус подписки", err);
  }
}

async function subscribeToPush() {
  const btn = document.getElementById("subscribe-btn");
  const hint = document.getElementById("push-hint");

  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    alert("Этот браузер не поддерживает push-уведомления");
    return;
  }

  if (!isStandalone()) {
    hint.classList.remove("hidden");
    return;
  }

  btn.disabled = true;
  btn.textContent = "Включаю…";

  try {
    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
      alert("Уведомления не разрешены в настройках iOS");
      btn.disabled = false;
      btn.textContent = "Включить уведомления";
      return;
    }

    const registration = await navigator.serviceWorker.register("sw.js");
    await navigator.serviceWorker.ready;

    const keyRes = await fetch("api/push/vapid-public-key");
    const { publicKey } = await keyRes.json();

    let subscription = await registration.pushManager.getSubscription();
    if (!subscription) {
      subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(publicKey),
      });
    }

    let deviceName = localStorage.getItem("deviceName");
    if (!deviceName) {
      deviceName = prompt("Как назвать это устройство?", "iPhone") || "Устройство";
      localStorage.setItem("deviceName", deviceName);
    }

    const raw = subscription.toJSON();
    await fetch("api/push/subscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ endpoint: raw.endpoint, keys: raw.keys, device_name: deviceName }),
    });

    setSubscribedUi();
  } catch (err) {
    console.error(err);
    alert("Не получилось включить уведомления: " + err.message);
    btn.disabled = false;
    btn.textContent = "Включить уведомления";
  }
}

async function submitReport(event) {
  event.preventDefault();
  const status = document.getElementById("report-status");
  const direction = document.getElementById("report-direction").value;
  const price = parseFloat(document.getElementById("report-price").value);
  const etaRaw = document.getElementById("report-eta").value;
  const eta_min = etaRaw ? parseInt(etaRaw, 10) : null;

  const res = await fetch("api/report_price", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ direction, price, eta_min }),
  });
  const data = await res.json();

  if (!res.ok) {
    status.textContent = data.error || "Ошибка";
    status.style.color = "var(--bad)";
    return;
  }

  status.textContent = "Записано";
  status.style.color = "var(--good)";
  document.getElementById("report-form").reset();
  loadStatus();
}

const REFRESH_INTERVAL_MS = 30000;

document.getElementById("subscribe-btn").addEventListener("click", subscribeToPush);
document.getElementById("report-form").addEventListener("submit", submitReport);

if ("serviceWorker" in navigator) {
  navigator.serviceWorker
    .register("sw.js")
    .then(() => checkPushStatus())
    .catch((err) => console.warn("SW register failed", err));
}

loadStatus();
setInterval(loadStatus, REFRESH_INTERVAL_MS);

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") loadStatus();
});
