// account.js: makes the profile card work.
// Clicking Save keeps your details in this browser (localStorage)
// and updates the name and avatar at the top of the card.

// Key the profile is saved under in the browser
const PROFILE_KEY = "profile";

// The form fields we save, by their id in index.html
const FIELDS = ["name", "email", "location", "bio"];

const form = document.querySelector(".profile form");
const nameText = document.querySelector(".profile-header .name");
const avatar = document.querySelector(".profile-header .avatar");

// Read the saved profile (or an empty one if nothing is saved / storage is blocked)
function loadProfile() {
  try {
    return JSON.parse(localStorage.getItem(PROFILE_KEY)) || {};
  } catch {
    return {};
  }
}

// Show the name and its first letter in the avatar circle
function showHeader(profile) {
  const name = (profile.name || "").trim();
  nameText.textContent = name || "Your Name";
  avatar.textContent = name ? name[0].toUpperCase() : "?";
}

// 1. When the page opens: fill the form with what was saved before
const saved = loadProfile();
FIELDS.forEach(id => {
  document.getElementById(id).value = saved[id] || "";
});
showHeader(saved);

// 2. When Save is clicked: collect the fields, store them, update the header
form.addEventListener("submit", e => {
  e.preventDefault(); // stop the page from reloading

  const profile = {};
  FIELDS.forEach(id => {
    profile[id] = document.getElementById(id).value.trim();
  });

  try {
    localStorage.setItem(PROFILE_KEY, JSON.stringify(profile));
  } catch (err) {
    console.log("couldn't save profile", err); // e.g. private browsing
  }
  showHeader(profile);
});
