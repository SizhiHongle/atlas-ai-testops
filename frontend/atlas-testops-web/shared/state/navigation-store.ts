"use client";

import { create } from "zustand";

type NavigationState = {
  mobileNavigationOpen: boolean;
  closeMobileNavigation: () => void;
  toggleMobileNavigation: () => void;
};

export const useNavigationStore = create<NavigationState>((set) => ({
  mobileNavigationOpen: false,
  closeMobileNavigation: () => set({ mobileNavigationOpen: false }),
  toggleMobileNavigation: () =>
    set((state) => ({ mobileNavigationOpen: !state.mobileNavigationOpen }))
}));
