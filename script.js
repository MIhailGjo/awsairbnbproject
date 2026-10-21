const SEARCH_URL = "https://bevdt4po5mvfmm7w4xmm3xwhtq0upqpu.lambda-url.us-east-1.on.aws/";

// Escape API values before putting them into innerHTML
function esc(value) {
  const div = document.createElement("div");
  div.textContent = value ?? "";
  return div.innerHTML;
}

document.getElementById("searchBtn").addEventListener("click", async () => {
  const addr = document.getElementById("searchInput").value.trim();
  const out = document.getElementById("results");
  if (!addr) return;

  out.textContent = "Searching...";
  try {
    const res = await fetch(SEARCH_URL + "?address=" + encodeURIComponent(addr));
    const data = await res.json();

    if (!res.ok) { out.textContent = data.error; return; }

    out.innerHTML = `
      <h3>${esc(data.formattedAddress)}</h3>
      <p>$${esc(data.pricePerNight)} / night · ${esc(data.beds)} beds · ${esc(data.baths)} baths</p>
      <small>${data.cached ? "from database" : "new — just added"}</small>`;
  } catch (err) {
    out.textContent = "Something went wrong";
    console.log(err);
  }
});
