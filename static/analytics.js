(function () {
  const tokenMeta = document.querySelector('meta[name="pcm-cloudflare-beacon-token"]');
  const beaconToken = (tokenMeta?.content || "").trim();
  if (!beaconToken) {
    return;
  }

  const script = document.createElement("script");
  script.defer = true;
  script.src = "https://static.cloudflareinsights.com/beacon.min.js";
  script.setAttribute("data-cf-beacon", JSON.stringify({ token: beaconToken }));
  document.head.appendChild(script);
})();
