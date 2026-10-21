// account.js: log in, create an account, show the user's info, log out.
// It talks to the "customerdata" Lambda, which looks users up in the
// Aurora database and sends back their info.

// Web address of the customerdata Lambda
const ACCOUNT_URL = "https://ihl3rckvuaz5qehskeh2btpvhu0yxfeh.lambda-url.us-east-1.on.aws/";

// Key the logged-in user is saved under in the browser
const USER_KEY = "user";

// --- Page elements ---
const authCard = document.getElementById("authCard");
const profileCard = document.getElementById("profileCard");
const loginForm = document.getElementById("loginForm");
const signupForm = document.getElementById("signupForm");
const authMsg = document.getElementById("authMsg");

// Send a request to the Lambda, e.g. { action: "login", email: "..." }
async function accountApi(body) {
  const res = await fetch(ACCOUNT_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Something went wrong");
  return data; // the user: { user_id, name, email, home_city, member_since }
}

// --- Remember who's logged in (stays after reloading the page) ---
function loadUser() {
  try {
    return JSON.parse(localStorage.getItem(USER_KEY));
  } catch {
    return null;
  }
}

function saveUser(user) {
  try {
    if (user) localStorage.setItem(USER_KEY, JSON.stringify(user));
    else localStorage.removeItem(USER_KEY);
  } catch (err) {
    console.log("couldn't remember login", err); // e.g. private browsing
  }
}

// Put the user's info from the database into the profile card
function showProfile(user) {
  const name = user.name || "";
  document.getElementById("profileAvatar").textContent = name ? name[0].toUpperCase() : "?";
  document.getElementById("profileName").textContent = name;
  // "2026-10-21" -> "Member since 2026"
  document.getElementById("profileSince").textContent =
    user.member_since ? "Member since " + user.member_since.slice(0, 4) : "";
  document.getElementById("profileEmail").textContent = user.email;
  document.getElementById("profileCity").textContent = user.home_city || "—";

  // "My Airbnbs": one line per Airbnb the user added
  const airbnbs = user.airbnbs || [];
  document.getElementById("profileAirbnbs").innerHTML = airbnbs.length
    ? airbnbs.map(a => `<li><strong>${esc(a.formatted_address)}</strong>
        <span>$${esc(a.price_per_night)} / night · ${esc(a.beds)} beds · ${esc(a.baths)} baths</span></li>`).join("")
    : "<li class=\"empty\">None yet. Search for one and click “Add to my Airbnbs”.</li>";
}

// --- "Add to my Airbnbs" button under the search result ---
let shownAddress = null; // the address of the search result on screen

// Show the right thing for this address: log in hint, button, or "added"
function showAddButton(address) {
  shownAddress = address;
  const box = document.getElementById("addAirbnb");
  if (!box) return; // no search result on screen

  const user = loadUser();
  if (!user) {
    box.innerHTML = "<small>Log in to add this Airbnb to your account.</small>";
    return;
  }
  const alreadyAdded = (user.airbnbs || []).some(a => a.address === address);
  box.innerHTML = alreadyAdded
    ? '<button type="button" class="save" disabled>✓ In my Airbnbs</button>'
    : '<button type="button" class="save" id="addAirbnbBtn">Add to my Airbnbs</button>';
  if (alreadyAdded) return;

  // Click: save it to the user (Aurora) and mark it in DynamoDB, through the Lambda
  document.getElementById("addAirbnbBtn").addEventListener("click", async e => {
    e.target.disabled = true;
    e.target.textContent = "Adding...";
    try {
      const data = await accountApi({ action: "add_airbnb", user_id: user.user_id, address });
      user.airbnbs = data.airbnbs; // the updated list from the database
      saveUser(user);
      showProfile(user);
      showAddButton(address);
    } catch (err) {
      e.target.disabled = false;
      e.target.textContent = "Add to my Airbnbs";
      box.insertAdjacentHTML("beforeend", `<small class="add-error">${esc(err.message)}</small>`);
    }
  });
}

// Switch the page between "logged in" and "logged out"
function render() {
  const user = loadUser();
  authCard.hidden = !!user;     // forms only when logged out
  profileCard.hidden = !user;   // profile only when logged in
  if (user) showProfile(user);
  if (shownAddress) showAddButton(shownAddress); // update the button for the new state
}

// Show the Log in or Create account form
function showForm(form) {
  [loginForm, signupForm].forEach(f => { f.hidden = f !== form; });
  document.querySelectorAll(".tab").forEach(t => {
    t.classList.toggle("active", t.dataset.tab + "Form" === form.id);
  });
  authMsg.textContent = "";
}

document.querySelectorAll(".tab").forEach(tab => {
  tab.addEventListener("click", () => showForm(document.getElementById(tab.dataset.tab + "Form")));
});

// After the Lambda sends back a user: remember them and show their info
function logIn(user) {
  saveUser(user);
  loginForm.reset();
  signupForm.reset();
  authMsg.textContent = "";
  render();
}

// 1. Log in: look the email up in the database
loginForm.addEventListener("submit", async e => {
  e.preventDefault();
  authMsg.textContent = "Logging in..."; // the database can take a few seconds to wake up
  try {
    logIn(await accountApi({
      action: "login",
      email: document.getElementById("loginEmail").value,
    }));
  } catch (err) {
    authMsg.textContent = err.message;
  }
});

// 2. Create account: add a new row to the users table
signupForm.addEventListener("submit", async e => {
  e.preventDefault();
  authMsg.textContent = "Creating account...";
  try {
    logIn(await accountApi({
      action: "signup",
      name: document.getElementById("signupName").value,
      email: document.getElementById("signupEmail").value,
      home_city: document.getElementById("signupCity").value,
    }));
  } catch (err) {
    authMsg.textContent = err.message;
  }
});

// 3. Log out: forget the user and go back to the log in form
document.getElementById("logoutBtn").addEventListener("click", () => {
  saveUser(null);
  showForm(loginForm);
  render();
});

// When the page opens: show the right state
render();
