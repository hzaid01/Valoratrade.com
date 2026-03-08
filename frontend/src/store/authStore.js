import { create } from 'zustand';
import {
  auth,
  signInWithEmailAndPassword,
  createUserWithEmailAndPassword,
  signOut,
  onAuthStateChanged
} from '../lib/firebase';

export const useAuthStore = create((set, get) => ({
  user: null,
  loading: true,
  _unsubscribe: null,

  setUser: (user) => set({ user }),
  setLoading: (loading) => set({ loading }),

  signUp: async (email, password) => {
    const userCredential = await createUserWithEmailAndPassword(auth, email, password);
    return userCredential;
  },

  signIn: async (email, password) => {
    const userCredential = await signInWithEmailAndPassword(auth, email, password);
    set({ user: userCredential.user });
    return userCredential;
  },

  signOut: async () => {
    await signOut(auth);
    set({ user: null });
  },

  initialize: async () => {
    // Cleanup any existing subscription to prevent memory leaks
    const currentUnsubscribe = get()._unsubscribe;
    if (currentUnsubscribe) {
      currentUnsubscribe();
    }

    set({ loading: true });

    // Set up auth state listener
    const unsubscribe = onAuthStateChanged(auth, (user) => {
      set({
        user: user ?? null,
        loading: false
      });
    });

    set({ _unsubscribe: unsubscribe });
  },

  // Cleanup method for component unmount
  cleanup: () => {
    const unsubscribe = get()._unsubscribe;
    if (unsubscribe) {
      unsubscribe();
      set({ _unsubscribe: null });
    }
  }
}));
