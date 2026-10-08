// GPS never leaves the phone except to your own laptop.

// Watch the position for up to `timeoutMs`, keep the most accurate fix, stop early once it's good enough.
export function getFix({ timeoutMs = 15000, goodEnoughM = 12 } = {}) {
  return new Promise((resolve) => {
    if (!('geolocation' in navigator)) return resolve(null);
    let best = null;
    const done = () => {
      navigator.geolocation.clearWatch(id);
      clearTimeout(timer);
      resolve(best);
    };
    const id = navigator.geolocation.watchPosition(
      (p) => {
        const fix = { lat: p.coords.latitude, lng: p.coords.longitude, accuracy: p.coords.accuracy };
        if (!best || fix.accuracy < best.accuracy) best = fix;
        if (best.accuracy <= goodEnoughM) done();
      },
      (err) => { if (err.code === err.PERMISSION_DENIED) done(); }, // other errors: keep waiting until timeout
      { enableHighAccuracy: true, maximumAge: 5000, timeout: timeoutMs },
    );
    const timer = setTimeout(done, timeoutMs);
  });
}

export function distanceM(a, b) {
  const R = 6371000, rad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * rad, dLng = (b.lng - a.lng) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}
