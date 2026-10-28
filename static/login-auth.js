import { auth, provider, facebookProvider } from "./firebase-config.js";
import { createUserWithEmailAndPassword, signInWithEmailAndPassword, signInWithPopup, sendPasswordResetEmail, sendEmailVerification } 
from "https://www.gstatic.com/firebasejs/12.15.0/firebase-auth.js";

const signInWithGoogleButtonEl = document.getElementById("sign-in-with-google-btn");
const signUpWithGoogleButtonEl = document.getElementById("sign-up-with-google-btn");
const signInWithFacebookButtonEl = document.getElementById("sign-in-with-facebook-btn");
const signUpWithFacebookButtonEl = document.getElementById("sign-up-with-facebook-btn");

const emailInputEl = document.getElementById("email-input");
const passwordInputEl = document.getElementById("password-input");
const signInButtonEl = document.getElementById("sign-in-btn");
const createAccountButtonEl = document.getElementById("create-account-btn");
const emailForgotPasswordEl = document.getElementById("email-forgot-password");
const forgotPasswordButtonEl = document.getElementById("forgot-password-btn");

const loginFormEl = document.querySelector(".auth-form-vertical"); 
const signupFormEl = document.querySelector(".auth-form-grid");

const errorMsgEmail = document.getElementById("email-error-message");
const errorMsgPassword = document.getElementById("password-error-message");
const errorMsgGoogleSignIn = document.getElementById("google-signin-error-message");

if (loginFormEl) {
    loginFormEl.addEventListener("submit", (e) => {
        e.preventDefault(); 
        authSignInWithEmail();
    });
}

if (signupFormEl) {
    signupFormEl.addEventListener("submit", (e) => {
        e.preventDefault(); 
        authCreateAccountWithEmail();
    });
}

if (signInWithGoogleButtonEl) {
    signInWithGoogleButtonEl.addEventListener("click", authSignInWithGoogle);
}
if (signUpWithGoogleButtonEl) {
    signUpWithGoogleButtonEl.addEventListener("click", authSignUpWithGoogle);
}
if (signInWithFacebookButtonEl) {
    signInWithFacebookButtonEl.addEventListener("click", authSignInWithFacebook);
}
if (signUpWithFacebookButtonEl) {
    signUpWithFacebookButtonEl.addEventListener("click", authSignUpWithFacebook);
}
if (forgotPasswordButtonEl) {
    forgotPasswordButtonEl.addEventListener("click", resetPassword);
}

//Google Auth
async function authSignInWithGoogle() {
    provider.setCustomParameters({
        'prompt': 'select_account'
    });
    try {
        const result = await signInWithPopup(auth, provider);
        processSocialAuthResult(result);
    } catch (error) {
        handleSocialAuthError(error, "Google Sign-In failed. Please try again.");
    }
}
    
async function authSignUpWithGoogle() {
    provider.setCustomParameters({
        'prompt': 'select_account'
    });
    try {
        const result = await signInWithPopup(auth, provider);
        processSocialAuthResult(result);
    } catch (error) {
        console.error("Error during Google signup: ", error.message);
        if (errorMsgGoogleSignIn) {
            errorMsgGoogleSignIn.textContent = "Google Sign-Up failed. Please try again.";
        }
    }
}

//Facebook Auth
async function authSignInWithFacebook() {
    try {
        const result = await signInWithPopup(auth, facebookProvider);
        processSocialAuthResult(result);
    } catch (error) {
        handleSocialAuthError(error, "Facebook Sign-In failed. Please try again.");
    }
}

async function authSignUpWithFacebook() {
    try {
        const result = await signInWithPopup(auth, facebookProvider);
        processSocialAuthResult(result);
    } catch (error) {
        console.error("Error during Facebook signup: ", error.message);
        if (errorMsgGoogleSignIn) {
            errorMsgGoogleSignIn.textContent = "Facebook Sign-Up failed. Please try again.";
        }
    }
}

async function processSocialAuthResult(result) {
    if (!result || !result.user) {
        throw new Error('Authentication failed: No user data returned.');
    }

    const user = result.user;
    const email = user.email || (user.providerData && user.providerData[0]?.email) || "";
    let derivedUsername = "";

    if (email) {
        derivedUsername = email.split('@')[0];
    } else if (user.displayName) {
        derivedUsername = user.displayName.toLowerCase().replace(/\s+/g, '');
    }

    const idToken = await user.getIdToken();
    const nameParts = (user.displayName || "").split(" ");
    
    const extraData = {
        first_name: nameParts[0] || "",
        last_name: nameParts.slice(1).join(" ") || "",
        username: derivedUsername, 
        contact: user.phoneNumber || "",
        address: "",
        photo_url: user.photoURL || ""
    };

    loginUser(user, idToken, extraData);
}

function handleSocialAuthError(error, customMessage) {
    if (error.code === "auth/user-disabled") {
        const blockedMsg = "Your account has been blocked by an administrator. Please contact support.";
        alert(blockedMsg);
        if (errorMsgGoogleSignIn) errorMsgGoogleSignIn.textContent = blockedMsg;
        return;
    }
    handleLogging(error, customMessage);
}

function authSignInWithEmail() {
    const email = emailInputEl.value;
    const password = passwordInputEl.value;

    signInWithEmailAndPassword(auth, email, password)
        .then((userCredential) => {
            const user = userCredential.user;

            if (!user.emailVerified) {
                if (errorMsgPassword) {
                    errorMsgPassword.textContent = "Please verify your email before logging in. Check your inbox for the confirmation link.";
                }
                auth.signOut();
                return;
            }

            user.getIdToken().then(function(idToken) {
                loginUser(user, idToken);
            });
        })
        .catch((error) => {
            const errorCode = error.code;
            console.error("Error code: ", errorCode);

            if (errorCode === "auth/user-disabled") {
                const blockedMsg = "Your account has been blocked by an administrator. Please contact support.";
                alert(blockedMsg);
                if (errorMsgPassword) errorMsgPassword.textContent = blockedMsg;
                return;
            }

            if (errorCode === "auth/invalid-email" && errorMsgEmail) {
                errorMsgEmail.textContent = "Invalid email";
            } else if (errorCode === "auth/invalid-credential" && errorMsgPassword) {
                errorMsgPassword.textContent = "Login failed - invalid email or password";
            } else if (errorMsgPassword) {
                errorMsgPassword.textContent = error.message;
            }
        });
}

function formatPhoneNumber(phoneInput) {
    if (!phoneInput) return "";
    let cleaned = phoneInput.replace(/\D/g, "");

    if (cleaned.startsWith("639")) {
        cleaned = "0" + cleaned.substring(2);
    } else if (cleaned.startsWith("9") && cleaned.length === 10) {
        cleaned = "0" + cleaned;
    } else if (!cleaned.startsWith("09") && cleaned.length === 9) {
        cleaned = "09" + cleaned;
    }
    return cleaned;
}

function authCreateAccountWithEmail() {
    const email = emailInputEl.value;
    const password = passwordInputEl.value;
    const confirmPasswordEl = document.getElementById("confirm-password-input");
    const confirmPassword = confirmPasswordEl ? confirmPasswordEl.value : "";
    const firstNameEl = document.getElementById("first-name-input");
    const lastNameEl = document.getElementById("last-name-input");
    const usernameEl = document.getElementById("username-input");
    const contactEl = document.getElementById("contact-input");
    const addressEl = document.getElementById("address-input");
    const errorMsgContact = document.getElementById("contact-error-message");

    if (errorMsgPassword) errorMsgPassword.textContent = "";
    if (errorMsgEmail) errorMsgEmail.textContent = "";
    if (errorMsgContact) errorMsgContact.textContent = "";
    if (password !== confirmPassword) {
        if (errorMsgPassword) {
            errorMsgPassword.textContent = "Passwords do not match.";
        }
        return;
    }

    const passwordRequirements = /^(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*()_+\-=\[\]{};':"\\|,.<>\/?]).{8,}$/;
    if (!passwordRequirements.test(password)) {
        if (errorMsgPassword) {
            errorMsgPassword.textContent = "Password must be at least 8 characters long and include 1 uppercase letter, 1 number, and 1 special character.";
        }
        return;
    }

    let formattedContact = formatPhoneNumber(contactEl ? contactEl.value : "");
    const phoneRegex = /^09\d{9}$/;
    if (!phoneRegex.test(formattedContact)) {
        if (errorMsgContact) {
            errorMsgContact.textContent = "Please enter a valid 11-digit phone number starting with 09 (e.g., 09170000000).";
        } else if (errorMsgPassword) {
            errorMsgPassword.textContent = "Invalid contact number format. Must be 11 digits starting with 09.";
        }
        return;
    }

    if (contactEl) contactEl.value = formattedContact;
    const extraData = {
        first_name: firstNameEl ? firstNameEl.value : "",
        last_name: lastNameEl ? lastNameEl.value : "",
        username: usernameEl ? usernameEl.value : "",
        contact: formattedContact,
        address: addressEl ? addressEl.value : ""
    };

    console.log("Starting account creation process...");
    createUserWithEmailAndPassword(auth, email, password)
        .then(async (userCredential) => {
            const user = userCredential.user;
            console.log("Firebase Auth User created:", user.uid);
            await sendEmailVerification(user);
            console.log("Verification email sent successfully.");
            const idToken = await user.getIdToken();
            await loginUser(user, idToken, extraData, true); 
        })
        .catch((error) => {
            const errorCode = error.code;
            console.error("Signup error caught:", error);

            if (errorCode === "auth/invalid-email" && errorMsgEmail) {
                errorMsgEmail.textContent = "Invalid email";
            } else if (errorCode === "auth/weak-password" && errorMsgPassword) {
                errorMsgPassword.textContent = "Password is too weak. Please meet all complexity requirements.";
            } else if (errorCode === "auth/email-already-in-use" && errorMsgEmail) {
                errorMsgEmail.textContent = "An account already exists for this email.";
            } else if (errorMsgPassword) {
                errorMsgPassword.textContent = error.message;
            }
        });
}

function resetPassword() {
    const emailToReset = emailForgotPasswordEl.value;
    clearInputField(emailForgotPasswordEl);
    sendPasswordResetEmail(auth, emailToReset)
    .then(() => {
        const resetFormView = document.getElementById("reset-password-view");
        const resetSuccessView = document.getElementById("reset-password-confirmation-page");

        if (resetFormView) resetFormView.style.display = "none";
        if (resetSuccessView) resetSuccessView.style.display = "block";
    })
    .catch((error) => {
        console.error("Password reset error: ", error.code, error.message);
    });
}

function loginUser(user, idToken, extraData = null, isRegistration = false) {
    const requestBody = {
        ...(extraData || {})
    };
    fetch('/auth', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${idToken}`
        },
        body: JSON.stringify(requestBody),
        credentials: 'same-origin'
    })
    .then(response => {
        if (response.ok || response.status === 403) {
            return response.json();
        }
        throw new Error('Backend authorization failed');
    })
    .then(data => {
        if (data.status === "blocked") {
            const blockedMessage = data.message || "Your account has been blocked by an administrator. Please contact support.";
            alert(blockedMessage);

            if (errorMsgPassword) {
                errorMsgPassword.textContent = blockedMessage;
            }
            if (errorMsgGoogleSignIn) {
                errorMsgGoogleSignIn.textContent = blockedMessage;
            }

            auth.signOut();
            return;
        }

        if (isRegistration || data.status === "unverified") {
            alert("Account created successfully! A verification link has been sent to your email. Please verify it before logging in.");
            auth.signOut().then(() => {
                window.location.href = "/login";
            });
        } else {
            if (data.status === "success" && data.redirect) {
                window.location.href = data.redirect;
            } else {
                console.error('Failed to log in:', data.message);
                if (errorMsgPassword) {
                    errorMsgPassword.textContent = data.message;
                }
            }
        }
    })
    .catch(error => {
        console.error('Error with Fetch operation: ', error);
    });
}

function handleLogging(error, ContextMessage) {
    console.error(`${ContextMessage}:`, error);
    if (errorMsgGoogleSignIn) {
        errorMsgGoogleSignIn.textContent = error.message || "An external authorization error occurred.";
    }
}

function clearInputField(field) {
    if (field) field.value = "";
}

document.addEventListener("DOMContentLoaded", () => {
    const togglePasswordIcons = document.querySelectorAll(".toggle-password");
    togglePasswordIcons.forEach(icon => {
        icon.addEventListener("click", function () {
            const passwordInput = this.parentElement.querySelector("input");
            if (passwordInput) {
                if (passwordInput.type === "password") {
                    passwordInput.type = "text";
                    this.classList.remove("fa-eye");
                    this.classList.add("fa-eye-slash");
                } else {
                    passwordInput.type = "password";
                    this.classList.remove("fa-eye-slash");
                    this.classList.add("fa-eye");
                }
            }
        });
    });

    const contactInput = document.getElementById("contact-input");
    if (contactInput) {
        contactInput.addEventListener("blur", function () {
            this.value = formatPhoneNumber(this.value);
        });
    }
});