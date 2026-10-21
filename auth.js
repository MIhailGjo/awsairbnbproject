// auth.js: sign up, log in and log out with Amazon Cognito.
// Cognito stores the users and passwords for us; this file just talks to it.

// --- Settings: fill these in from the Cognito user pool ---
const COGNITO_REGION = "us-east-1";
const COGNITO_CLIENT_ID = "5pv72ombm74c37snu4dc13ss09"; // User pool -> App clients

// Key the login tokens are saved under in the browser
const SESSION_KEY = "session";

// Send one request to Cognito; "action" is what we want it to do (SignUp, InitiateAuth...)
async function cognito(action, body) {
  const res = await fetch(`https://cognito-idp.${COGNITO_REGION}.amazonaws.com/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-amz-json-1.1",
      "X-Amz-Target": "AWSCognitoIdentityProviderService." + action,
    },
    body: JSON.stringify({ ClientId: COGNITO_CLIENT_ID, ...body }),
  });
  const data = await res.json();
  // Cognito sends back a readable message when something is wrong
  if (!res.ok) throw new Error(data.message || "Something went wrong");
  return data;
}

// Read the email out of Cognito's id token (the middle part is base64 JSON)
function emailFromToken(idToken) {
  const part = idToken.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
  return JSON.parse(atob(part)).email;
}

// --- Saved login (stays after reloading the page) ---
function loadSession() {
  try {
    const s = JSON.parse(localStorage.getItem(SESSION_KEY));
    // ignore it if the token has expired (Cognito tokens last 1 hour)
    return s && s.expiresAt > Date.now() ? s : null;
  } catch {
    return null;
  }
}

function saveSession(s) {
  try {
    if (s) localStorage.setItem(SESSION_KEY, JSON.stringify(s));
    else localStorage.removeItem(SESSION_KEY);
  } catch (err) {
    console.log("couldn't save login", err); // e.g. private browsing
  }
}

// --- Page elements ---
const authForms = document.getElementById("authForms");
const loggedIn = document.getElementById("loggedIn");
const loginForm = document.getElementById("loginForm");
const signupForm = document.getElementById("signupForm");
const confirmForm = document.getElementById("confirmForm");
const authMsg = document.getElementById("authMsg");
const profileCard = document.querySelector(".profile");

let pendingEmail = ""; // email waiting for its confirmation code

// Show one of the three forms and hide the others
function showForm(form) {
  [loginForm, signupForm, confirmForm].forEach(f => { f.hidden = f !== form; });
  document.querySelectorAll(".tab").forEach(t => {
    t.classList.toggle("active", t.dataset.tab + "Form" === form.id);
  });
  authMsg.textContent = "";
}

// Switch the page between "logged in" and "logged out"
function render() {
  const session = loadSession();
  authForms.hidden = !!session;
  loggedIn.hidden = !session;
  profileCard.hidden = !session; // profile only for logged-in users
  if (session) document.getElementById("userEmail").textContent = session.email;
}

// Tabs: Log in / Sign up
document.querySelectorAll(".tab").forEach(tab => {
  tab.addEventListener("click", () => showForm(document.getElementById(tab.dataset.tab + "Form")));
});

// 1. Sign up: create the user; Cognito emails them a code
signupForm.addEventListener("submit", async e => {
  e.preventDefault();
  const email = document.getElementById("signupEmail").value.trim();
  const password = document.getElementById("signupPassword").value;
  try {
    await cognito("SignUp", {
      Username: email,
      Password: password,
      UserAttributes: [{ Name: "email", Value: email }],
    });
    pendingEmail = email;
    showForm(confirmForm);
  } catch (err) {
    authMsg.textContent = err.message;
  }
});

// 2. Confirm: send the emailed code back to Cognito, then go to log in
confirmForm.addEventListener("submit", async e => {
  e.preventDefault();
  try {
    await cognito("ConfirmSignUp", {
      Username: pendingEmail,
      ConfirmationCode: document.getElementById("confirmCode").value.trim(),
    });
    document.getElementById("loginEmail").value = pendingEmail;
    showForm(loginForm);
    authMsg.textContent = "Account confirmed, you can log in now.";
  } catch (err) {
    authMsg.textContent = err.message;
  }
});

// 3. Log in: check email + password, save the tokens Cognito gives back
loginForm.addEventListener("submit", async e => {
  e.preventDefault();
  const email = document.getElementById("loginEmail").value.trim();
  try {
    const data = await cognito("InitiateAuth", {
      AuthFlow: "USER_PASSWORD_AUTH",
      AuthParameters: {
        USERNAME: email,
        PASSWORD: document.getElementById("loginPassword").value,
      },
    });
    const tokens = data.AuthenticationResult;
    saveSession({
      email: emailFromToken(tokens.IdToken),
      idToken: tokens.IdToken,
      accessToken: tokens.AccessToken,
      expiresAt: Date.now() + tokens.ExpiresIn * 1000,
    });
    loginForm.reset();
    render();
  } catch (err) {
    // signed up but never entered the code -> show the code form
    if (err.message.includes("not confirmed")) {
      pendingEmail = email;
      showForm(confirmForm);
    }
    authMsg.textContent = err.message;
  }
});

// 4. Log out: forget the tokens
document.getElementById("logoutBtn").addEventListener("click", () => {
  saveSession(null);
  showForm(loginForm);
  render();
});

// When the page opens: show the right state
render();
