import { initializeApp } from "https://www.gstatic.com/firebasejs/12.15.0/firebase-app.js";
import { getAuth, GoogleAuthProvider, FacebookAuthProvider } from "https://www.gstatic.com/firebasejs/12.15.0/firebase-auth.js";
import { getFirestore } from "https://www.gstatic.com/firebasejs/12.15.0/firebase-firestore.js";
import { getStorage } from "https://www.gstatic.com/firebasejs/12.15.0/firebase-storage.js";

const firebaseConfig = {
  apiKey: "AIzaSyAy9tkVqQO6UT51DaIb7Oi8C856LcZJA_Q",
  authDomain: "animal-shelter-6b0ef.firebaseapp.com",
  projectId: "animal-shelter-6b0ef",
  storageBucket: "animal-shelter-6b0ef.firebasestorage.app",
  messagingSenderId: "243896454584",
  appId: "1:243896454584:web:3747cae637a27e30f9534d"
};

const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
const provider = new GoogleAuthProvider();
const facebookProvider = new FacebookAuthProvider();

const storage = getStorage(app);
const db = getFirestore(app);

export { auth, provider, facebookProvider, db };