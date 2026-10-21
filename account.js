// account.js: makes the profile card work.
// Shows the logged-in user's details, and clicking Save updates
// them in the database (through the Lambda) and in the card header.

// The form fields, by their id in index.html
const FIELDS = ["name", "email", "location", "bio"];

const form = document.querySelector(".profile form");
const nameText = document.querySelector(".profile-header .name");
const avatar = document.querySelector(".profile-header .avatar");
const memberSince = document.querySelector(".profile-header .meta");
const profileMsg = document.getElementById("profileMsg");

// Fill the card with a user's details (called by auth.js after logging in)
function showProfile(user) {
  FIELDS.forEach(id => {
    document.getElementById(id).value = user[id] || "";
  });
  const name = (user.name || "").trim();
  nameText.textContent = name || "Your Name";
  avatar.textContent = name ? name[0].toUpperCase() : "?";
  // "2026-10-21T..." -> "Member since 2026"
  memberSince.textContent = user.createdAt ? "Member since " + user.createdAt.slice(0, 4) : "";
  profileMsg.textContent = "";
}

// When Save is clicked: send the changes to the database
form.addEventListener("submit", async e => {
  e.preventDefault(); // stop the page from reloading

  const changes = { action: "update" };
  FIELDS.forEach(id => {
    changes[id] = document.getElementById(id).value;
  });

  profileMsg.textContent = "Saving...";
  try {
    const user = await accountApi(changes); // from auth.js
    saveUser(user);                          // remember the new details
    showProfile(user);
    profileMsg.textContent = "Saved";
  } catch (err) {
    profileMsg.textContent = err.message;
  }
});
