# Superseded: ion-pair optimisations, builder v1 (run optimize_20261031T040154Z, 04:01-04:20Z)

These 53 ion-pair calculations (A-.H3O+.(n-1)H2O, contact + solvent-separated) are REAL ALCHEMI/AIMNet2
outputs but were started from geometries built by a buggy v1 proton-placement routine in
src/fermentai/molecular/structures.py: the transferred proton was placed 1.0 A from the receiving
water O along the O...H(acid) direction, which in 7 of 53 inputs put it 0.12-0.55 A from an existing
water H. Two of those (lactic_acid_ionpair__n3__c2, lactic_acid_ionpairSS__n3__c2) relaxed into
spurious, unphysical low-energy states with two H atoms ~0.13 A apart (energies ~0.75 eV below the
neutral minimum, FIRE2 not converged after 4000 steps) -- a known failure mode of ML potentials far
outside their training distribution.

All ion-pair inputs were regenerated with the fixed builder (v2: sphere-sampled placement with
clearance check) and re-optimised; the runner and the analysis now reject any geometry with two atoms
closer than 0.70 A. Nothing in this directory is used by data/alchemi/analysis/.
