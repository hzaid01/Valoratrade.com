import { initializeApp } from 'firebase/app';
import {
    getAuth,
    signInWithEmailAndPassword,
    createUserWithEmailAndPassword,
    signOut as firebaseSignOut,
    onAuthStateChanged
} from 'firebase/auth';

const firebaseConfig = {
    apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
    authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
    projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
    storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET,
    messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID,
    appId: import.meta.env.VITE_FIREBASE_APP_ID
};

// Validate required environment variables
const requiredVars = ['VITE_FIREBASE_API_KEY', 'VITE_FIREBASE_AUTH_DOMAIN', 'VITE_FIREBASE_PROJECT_ID'];
const missingVars = requiredVars.filter(v => !import.meta.env[v]);

if (missingVars.length > 0) {
    throw new Error(
        `Missing required Firebase environment variables: ${missingVars.join(', ')}. ` +
        'Please check your .env file.'
    );
}

// Initialize Firebase
const app = initializeApp(firebaseConfig);
export const auth = getAuth(app);

// Export auth methods for convenience
export {
    signInWithEmailAndPassword,
    createUserWithEmailAndPassword,
    firebaseSignOut as signOut,
    onAuthStateChanged
};

/**
 * Get the current user's ID token for API authentication.
 * @param {boolean} forceRefresh - Force token refresh
 * @returns {Promise<string|null>} The ID token or null if not authenticated
 */
export async function getIdToken(forceRefresh = false) {
    const user = auth.currentUser;
    if (!user) return null;

    try {
        return await user.getIdToken(forceRefresh);
    } catch (error) {
        console.error('Failed to get ID token:', error);
        return null;
    }
}
