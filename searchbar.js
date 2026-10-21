// searchbar.js: makes the search bar work.
// When you click Search, it sends the address to our AWS Lambda
// and shows the rental info it sends back.

// Web address of our Lambda function (the backend)
const SEARCH_URL = "https://bevdt4po5mvfmm7w4xmm3xwhtq0upqpu.lambda-url.us-east-1.on.aws/";

// Escape API values before putting them into innerHTML
// (turns characters like < and > into safe text so they can't run as code)
function esc(value) {
  const div = document.createElement("div");
  div.textContent = value ?? "";
  return div.innerHTML;
}

// --- Suggestions dropdown: show up to 5 saved addresses while typing ---
const input = document.getElementById("searchInput");
const list = document.getElementById("suggestions");
let typingTimer; // used to wait until the user pauses typing

input.addEventListener("input", () => {
  clearTimeout(typingTimer);
  const text = input.value.trim();
  if (text.length < 2) { list.innerHTML = ""; return; } // too short -> hide

  // wait 300ms after the last key so we don't call the Lambda on every letter
  typingTimer = setTimeout(async () => {
    try {
      const res = await fetch(SEARCH_URL + "?suggest=" + encodeURIComponent(text));
      const data = await res.json();
      if (input.value.trim() !== text) return; // user kept typing, ignore old answer

      // one <li> per saved address
      list.innerHTML = (data.suggestions || [])
        .map(s => `<li>${esc(s)}</li>`)
        .join("");
    } catch (err) {
      list.innerHTML = ""; // on error just hide the dropdown
      console.log(err);
    }
  }, 300);
});

// Clicking a suggestion puts it in the box and runs the search
list.addEventListener("click", e => {
  if (e.target.tagName !== "LI") return;
  input.value = e.target.textContent;
  list.innerHTML = "";
  document.getElementById("searchBtn").click();
});

// Clicking anywhere else closes the dropdown
document.addEventListener("click", e => {
  if (!e.target.closest(".search-wrap")) list.innerHTML = "";
});

// Run this code every time the Search button is clicked
document.getElementById("searchBtn").addEventListener("click", async () => {
  list.innerHTML = ""; // close the dropdown when searching
  // 1. Grab what the user typed and the box where results go
  const addr = document.getElementById("searchInput").value.trim();
  const out = document.getElementById("results");
  if (!addr) return; // nothing typed -> do nothing

  // 2. Show a loading message while we wait
  out.textContent = "Searching...";
  try {
    // 3. Call the Lambda with the address, e.g. ...?address=123%20Main%20St
    const res = await fetch(SEARCH_URL + "?address=" + encodeURIComponent(addr));
    const data = await res.json(); // read the JSON answer

    // 4. The Lambda said something went wrong -> show its error message
    if (!res.ok) { out.textContent = data.error; return; }

    // 5. Success -> show the address, price, beds, baths, and where it came from
    out.innerHTML = `
      <h3>${esc(data.formattedAddress)}</h3>
      <p>$${esc(data.pricePerNight)} / night · ${esc(data.beds)} beds · ${esc(data.baths)} baths</p>
      <small>${data.cached ? "from database" : "new — just added"}</small>
      <div id="addAirbnb" class="add-airbnb"></div>`;
    // "Reserve" button (account.js decides what to show)
    showAddButton(data.address, data.reserved);
  } catch (err) {
    // 6. Network problem (offline, Lambda down, CORS blocked, ...)
    out.textContent = "Something went wrong";
    console.log(err); // details show in the browser's developer console
  }
});
