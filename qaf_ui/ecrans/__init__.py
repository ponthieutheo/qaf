"""Écrans QAF : un module par écran, un processus par écran (docs/ECRANS-QAF.md).

Chaque écran sépare sa logique (sans GTK, testée sans affichage) de son rendu
(`*_gtk.py`, importé seulement au lancement par `main()`).
"""
