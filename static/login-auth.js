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
        photo_url:
            user.photoURL ||
            user.providerData?.find(
                provider => provider.providerId === "google.com"
            )?.photoURL ||
            user.providerData?.[0]?.photoURL ||
            ""
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
    const latitudeEl = document.getElementById("address-latitude");
    const longitudeEl = document.getElementById("address-longitude");
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

    const passwordRequirements = /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{8,}$/;
    if (!passwordRequirements.test(password)) {
        if (errorMsgPassword) {
            errorMsgPassword.textContent =
                "Please meet all the password requirements.";
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
        first_name: firstNameEl ? firstNameEl.value.trim() : "",
        last_name: lastNameEl ? lastNameEl.value.trim() : "",
        username: usernameEl ? usernameEl.value.trim() : "",
        contact: formattedContact,
        address: addressEl ? addressEl.value.trim() : "",
        latitude: latitudeEl && latitudeEl.value
            ? parseFloat(latitudeEl.value)
            : null,
        longitude: longitudeEl && longitudeEl.value
            ? parseFloat(longitudeEl.value)
            : null
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
    const passwordInput = document.getElementById("password-input");
    const passwordRequirementsContainer = document.getElementById("password-requirements");
    const confirmPasswordInput = document.getElementById("confirm-password-input");
    const confirmPasswordMessage = document.getElementById("confirm-password-message");
    const requirements = {
        length: {
            element: document.getElementById("length-requirement"),
            test: password => password.length >= 8
        },
        special: {
            element: document.getElementById("special-requirement"),
            test: password => /[^A-Za-z0-9]/.test(password)
        },
        number: {
            element: document.getElementById("number-requirement"),
            test: password => /\d/.test(password)
        },
        uppercase: {
            element: document.getElementById("uppercase-requirement"),
            test: password => /[A-Z]/.test(password)
        },
        lowercase: {
            element: document.getElementById("lowercase-requirement"),
            test: password => /[a-z]/.test(password)
        }
    };

    function hidePasswordFeedback() {
        if (passwordRequirementsContainer) {
            passwordRequirementsContainer.classList.remove("show");
        }
        if (confirmPasswordMessage) {
            confirmPasswordMessage.style.display = "none";
        }
    }

    function checkPasswordRequirements() {
        if (!passwordInput) return;
        const password = passwordInput.value;
        if (passwordRequirementsContainer) {
            if (password.length > 0) {
                passwordRequirementsContainer.classList.add("show");
            } else {
                passwordRequirementsContainer.classList.remove("show");
            }
        }

        for (const key in requirements) {
            const requirement = requirements[key];
            if (!requirement.element) continue;
            if (requirement.test(password)) {
                requirement.element.classList.add("valid");
            } else {
                requirement.element.classList.remove("valid");
            }
        }
        if (typeof errorMsgPassword !== "undefined" && errorMsgPassword) {
            errorMsgPassword.textContent = "";
        }
        checkPasswordMatch();
    }

    function checkPasswordMatch() {
        if (!passwordInput || !confirmPasswordInput || !confirmPasswordMessage) {
            return;
        }
        const password = passwordInput.value;
        const confirmPassword = confirmPasswordInput.value;
        if (confirmPassword.length === 0) {
            confirmPasswordMessage.textContent = "";
            confirmPasswordMessage.className = "password-match-message";
            return;
        }
        if (password.length === 0) {
            confirmPasswordMessage.textContent = "Please enter your password first.";
            confirmPasswordMessage.className = "password-match-message not-match";
            return;
        }
        if (password === confirmPassword) {
            confirmPasswordMessage.textContent = "✓ Passwords match.";
            confirmPasswordMessage.className = "password-match-message match";
        } else {
            confirmPasswordMessage.textContent = "✕ Passwords do not match.";
            confirmPasswordMessage.className = "password-match-message not-match";
        }
    }

    if (passwordInput) {
        passwordInput.addEventListener("input", checkPasswordRequirements);
        passwordInput.addEventListener("focus", function () {
            if (confirmPasswordMessage) {
                confirmPasswordMessage.style.display = "none";
            }
            if (this.value.length > 0 && passwordRequirementsContainer) {
                passwordRequirementsContainer.classList.add("show");
            }
        });
    }

    if (confirmPasswordInput) {
        confirmPasswordInput.addEventListener("input", checkPasswordMatch);
        confirmPasswordInput.addEventListener("focus", function () {
            if (passwordRequirementsContainer) {
                passwordRequirementsContainer.classList.remove("show");
            }
            if (this.value.length > 0 && confirmPasswordMessage) {
                checkPasswordMatch();
                confirmPasswordMessage.style.display = "block";
            }
        });
    }

    const allFormInputs = document.querySelectorAll(".auth-form-grid input");
    allFormInputs.forEach(input => {
        input.addEventListener("focus", function () {
            if (input !== passwordInput && input !== confirmPasswordInput) {
                hidePasswordFeedback();
            }
        });
    });

    const togglePasswordIcons = document.querySelectorAll(".toggle-password");
    togglePasswordIcons.forEach(icon => {
        icon.addEventListener("click", function () {
            const input = this.parentElement.querySelector("input");
            if (!input) return;
            if (input.type === "password") {
                input.type = "text";
                this.classList.remove("fa-eye");
                this.classList.add("fa-eye-slash");
            } else {
                input.type = "password";
                this.classList.remove("fa-eye-slash");
                this.classList.add("fa-eye");
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

document.addEventListener("DOMContentLoaded", () => {
    const addressInput = document.getElementById("address-input");
    const locationStatus = document.getElementById("location-status");
    const latitudeInput = document.getElementById("address-latitude");
    const longitudeInput = document.getElementById("address-longitude");
    if (!addressInput || !locationStatus) {
        return;
    }

    function updateLocationStatus(message, color) {
        locationStatus.textContent = message;
        locationStatus.style.color = color;
    }

    if (!navigator.geolocation) {
        updateLocationStatus(
            "Your browser does not support automatic location detection. Please enter your address manually.",
            "red"
        );
        return;
    }

    navigator.geolocation.getCurrentPosition(
        async (position) => {
            const latitude = position.coords.latitude;
            const longitude = position.coords.longitude;
            try {
                updateLocationStatus(
                    "Retrieving your current address...",
                    "#5d888f"
                );

                const response = await fetch(
                    `https://photon.komoot.io/reverse?lat=${latitude}&lon=${longitude}`
                );

                if (!response.ok) {
                    throw new Error("Address lookup failed.");
                }

                const data = await response.json();
                if (!data.features || data.features.length === 0) {
                    throw new Error("No address found.");
                }

                const place = data.features[0].properties;
                const addressParts = [];
                if (place.housenumber && place.street) {
                    addressParts.push(
                        `${place.housenumber} ${place.street}`
                    );
                } else if (place.street) {
                    addressParts.push(place.street);
                } else if (place.name) {
                    addressParts.push(place.name);
                }
                if (place.suburb) {
                    addressParts.push(place.suburb);
                }
                if (place.district && place.district !== place.suburb) {
                    addressParts.push(place.district);
                }
                if (place.city) {
                    addressParts.push(place.city);
                } else if (place.town) {
                    addressParts.push(place.town);
                } else if (place.village) {
                    addressParts.push(place.village);
                }
                if (place.state) {
                    addressParts.push(place.state);
                }
                if (place.postcode) {
                    addressParts.push(place.postcode);
                }
                if (place.country) {
                    addressParts.push(place.country);
                }

                const formattedAddress = [...new Set(addressParts)]
                    .join(", ");

                if (!formattedAddress) {
                    throw new Error("Unable to format the address.");
                }

                addressInput.value = formattedAddress;
                latitudeInput.value = latitude;
                longitudeInput.value = longitude;
                updateLocationStatus(
                    "Your current address was detected. Please verify it before registering.",
                    "green"
                );
            } catch (error) {
                console.error("Address detection error:", error);
                updateLocationStatus(
                    "Unable to retrieve your address. Please enter it manually.",
                    "red"
                );
                addressInput.placeholder = "Enter your complete address";
            }
        },

        (error) => {
            let message = "Unable to detect your location. Please enter your address manually.";
            if (error.code === error.PERMISSION_DENIED) {
                message = "Location permission denied. Please enter your address manually.";
            } else if (error.code === error.TIMEOUT) {
                message = "Location request timed out. Please enter your address manually.";
            } else if (error.code === error.POSITION_UNAVAILABLE) {
                message = "Your location is unavailable. Please enter your address manually.";
            }
            updateLocationStatus(message, "red");
            addressInput.placeholder = "Enter your complete address";
        },

        {
            enableHighAccuracy: true,
            timeout: 20000,
            maximumAge: 0
        }
    );

    const agreementCheckbox = document.getElementById("agreement-checkbox");
    const agreementModal = document.getElementById("agreement-modal");
    const agreementBody = document.getElementById("agreement-modal-body");
    const openAgreementButton = document.getElementById("open-agreement-modal");
    const closeAgreementButton = document.getElementById("close-agreement-modal");
    const acceptAgreementButton = document.getElementById("accept-agreement-btn");
    const agreementReadStatus = document.getElementById("agreement-read-status");
    const agreementError = document.getElementById("agreement-error");
    
    let agreementFullyRead = false;
    if (openAgreementButton) {
        openAgreementButton.addEventListener("click", function (event) {
            event.preventDefault();
            agreementModal.classList.add("active");
            agreementModal.setAttribute("aria-hidden", "false");
            document.body.style.overflow = "hidden";
            agreementBody.scrollTop = 0;
            agreementFullyRead = false;
            acceptAgreementButton.disabled = true;
            agreementReadStatus.classList.remove("completed");
            agreementReadStatus.innerHTML =
                '<i class="fa-solid fa-arrow-down"></i> Scroll to the bottom to continue.';
            checkAgreementReading();
        });
    }

    function checkAgreementReading() {
        if (!agreementBody || !acceptAgreementButton) return;

        const reachedBottom =
            agreementBody.scrollTop + agreementBody.clientHeight >=
            agreementBody.scrollHeight - 5;

        if (reachedBottom) {
            agreementFullyRead = true;
            acceptAgreementButton.disabled = false;

            agreementReadStatus.classList.add("completed");
            agreementReadStatus.innerHTML =
                '<i class="fa-solid fa-circle-check"></i> You have reached the end of the agreement.';
        }
    }

    if (agreementBody) {
        agreementBody.addEventListener("scroll", checkAgreementReading);
    }

    if (acceptAgreementButton) {
        acceptAgreementButton.addEventListener("click", function () {
            if (!agreementFullyRead) return;

            agreementCheckbox.disabled = false;
            agreementCheckbox.checked = true;
            agreementModal.classList.remove("active");
            agreementModal.setAttribute("aria-hidden", "true");
            document.body.style.overflow = "";

            if (agreementError) {
                agreementError.textContent = "";
            }
        });
    }

    if (closeAgreementButton) {
        closeAgreementButton.addEventListener("click", function () {
            agreementModal.classList.remove("active");
            agreementModal.setAttribute("aria-hidden", "true");

            document.body.style.overflow = "";
        });
    }

    const registerForm = document.querySelector(".auth-form-grid");
    if (registerForm) {
        registerForm.addEventListener("submit", function (event) {
            if (!agreementCheckbox || !agreementCheckbox.checked) {
                event.preventDefault();
                event.stopImmediatePropagation();

                if (agreementError) {
                    agreementError.textContent =
                        "Please read and agree to the Terms of Service & Privacy Policy before creating your account.";
                }

                if (openAgreementButton) {
                    openAgreementButton.focus();
                }
            }
        }, true);
    }
});