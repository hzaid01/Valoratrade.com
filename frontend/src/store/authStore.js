import { create } from 'zustand';
import { supabase } from '../lib/supabase';

export const useAuthStore = create((set, get) => ({
  user: null,
  session: null,
  loading: true,
  _unsubscribe: null,

  setUser: (user) => set({ user }),
  setSession: (session) => set({ session }),
  setLoading: (loading) => set({ loading }),

  signUp: async (email, password) => {
    const { data, error } = await supabase.auth.signUp({
      email,
      password,
    });
    if (error) throw error;
    return data;
  },

  signIn: async (email, password) => {
    const { data, error } = await supabase.auth.signInWithPassword({
      email,
      password,
    });
    if (error) throw error;
    set({ user: data.user, session: data.session });
    return data;
  },

  signOut: async () => {
    const { error } = await supabase.auth.signOut();
    if (error) throw error;
    set({ user: null, session: null });
  },

  initialize: async () => {
    // Cleanup any existing subscription to prevent memory leaks
    const currentUnsubscribe = get()._unsubscribe;
    if (currentUnsubscribe) {
      currentUnsubscribe();
    }

    set({ loading: true });

    try {
      const { data: { session } } = await supabase.auth.getSession();

      // Set up auth state listener
      const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, session) => {
        set({
          session,
          user: session?.user ?? null
        });
      });

      set({
        session,
        user: session?.user ?? null,
        loading: false,
        _unsubscribe: () => subscription.unsubscribe()
      });
    } catch (error) {
      console.error('Auth initialization error:', error);
      set({
        session: null,
        user: null,
        loading: false
      });
    }
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
