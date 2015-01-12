import numpy as np
from ase.io import read
from ase.units import J, kJ, mol, _hplanck, m, kg
from ase.thermochemistry import IdealGasThermo, HarmonicThermo
from masses import masses


class _Thermo(object):
    """Generic thermodynamics object"""
    def __init__(self, outcar, T=298.15, P=101325, linear=False, symm=1, \
            spin=0., ts=False, h_scale=1.0, s_scale=1.0, fixed=False, label=None):
        self.outcar = outcar
        self.atoms = read(self.outcar, index=0)
        self.mass = [masses[atom.symbol] for atom in self.atoms]
        self.atoms.set_masses(self.mass)
        self._read_hess()
        self.T = T
        self.P = P
        self.geometry = 'linear' if linear else 'nonlinear'
        self.e_elec = self.atoms.get_potential_energy()
        self.symm = symm
        self.spin = spin
        self.ts = ts
        self.h_scale = h_scale
        self.s_scale = s_scale
        self.fixed = fixed
        self.label = label
    def get_enthalpy(self, T=None):
        if T is None:
            T = self.T
        return self.thermo.get_internal_energy(T, verbose=False) * self.h_scale
    def get_entropy(self, T=None, P=None):
        if T is None:
            T = self.T
        return self.thermo.get_entropy(T, verbose=False) * self.s_scale
    def get_energy(self):
        return self.e_elec
    def copy(self):
        raise NotImplementedError
    def _read_hess(self):
        # This reads the hessian from the OUTCAR and diagonalizes it
        # to find the frequencies, rather than reading the frequencies
        # directly from the OUTCAR. This is to ensure we use the same
        # unit conversion factors, and also to make sure we use the same
        # atom masses for all calculations. Also, allows for the possibility
        # of doing partial hessian diagonalization should we want to do that.
        hessblock = 0
        with open(self.outcar, 'r') as f:
            for line in f:
                line = line.strip()
                if line != '':
                    if hessblock == 1:
                        if line.startswith('---'):
                            hessblock = 2
                    elif hessblock == 2:
                        line = line.split()
                        dof = len(line)
                        self.hess = np.zeros((dof, dof), dtype=float)
                        index = np.zeros(dof, dtype=int)
                        cart = np.zeros(dof, dtype=int)
                        for i, direction in enumerate(line):
                            index[i] = int(direction[:-1]) - 1
                            if direction[-1] == 'X':
                                cart[i] = 0
                            elif direction[-1] == 'Y':
                                cart[i] = 1
                            elif direction[-1] == 'Z':
                                cart[i] = 2
                            else:
                                raise ValueError, "Error reading Hessian!"
                        hessblock = 3
                        j = 0
                    elif hessblock == 3:
                        line = line.split()
                        self.hess[j] = np.array([float(val) for val in line[1:]], \
                                dtype=float)
                        j += 1
                    elif line.startswith('SECOND DERIVATIVES'):
                        hessblock = 1
                elif hessblock == 3:
                    break

        self.hess = -(self.hess + self.hess.T) / 2.
        mass = np.array([self.atoms[i].mass for i in index], dtype=float)
        self.hess /= np.sqrt(np.outer(mass, mass))
        self.hess *= _hplanck**2 * J * m**2 * kg / (4 * np.pi**2)
        v, w = np.linalg.eig(self.hess)
        # We're taking the square root of an array that could include
        # negative numbers, so the result has to be complex.
        freq = np.sqrt(np.array(v, dtype=complex))
        self.freqs = np.zeros_like(freq, dtype=float)
        # We don't want to deal with complex numbers, so we just convert
        # imaginary numbers to negative reals.
        for i, val in enumerate(freq):
            if val.imag == 0:
                self.freqs[i] = val.real
            else:
                self.freqs[i] = -val.imag
        self.freqs.sort()
    def __repr__(self):
        if self.label is not None:
            return self.label
        else:
            return self.atoms.get_chemical_formula()
    def __add__(self, other):
        return _Reactants([self, other])
    def __mul__(self, factor):
        assert isinstance(factor, int)
        return _Reactants([self for i in xrange(factor)])
    def __rmul__(self, factor):
        return self.__mul__(factor)

class IdealGas(_Thermo):
    def __init__(self, outcar, T=298.15, P=101325, linear=False, symm=1, \
            spin=0., D=None, h_scale=1.0, s_scale=1.0, fixed=True, label=None):
        super(IdealGas, self).__init__(outcar, T, P, linear, symm, \
                spin, False, h_scale, s_scale, fixed, label)
        self.gas = True
        self.D = D
        assert np.all(self.freqs[6:] > 0), "Imaginary frequencies found!"
        self.thermo = IdealGasThermo(
                self.freqs[6:],
                self.geometry,
                electronicenergy=self.e_elec,
                symmetrynumber=self.symm,
                spin=self.spin,
                atoms=self.atoms,
                )
    def get_enthalpy(self, T=None):
        if T is None:
            T = self.T
        return self.thermo.get_enthalpy(T, verbose=False) * self.h_scale
    def get_entropy(self, T=None, P=None):
        if T is None:
            T = self.T
        if P is None:
            P = self.P
        return self.thermo.get_entropy(T, P, verbose=False) * self.s_scale
    def copy(self):
        linear = True if self.geometry == 'linear' else False
        return self.__class__(self.outcar, self.T, self.P, linear, \
                self.symm, self.spin)

class Harmonic(_Thermo):
    def __init__(self, outcar, T=298.15, ts=False, coord=1, h_scale=1.0, \
            s_scale=1.0, fixed=False, label=None):
        super(Harmonic, self).__init__(outcar, T, None, None, None, \
                None, ts, h_scale, s_scale, fixed, label)
        self.gas = False
        nimag = 1 if ts else 0
        assert np.all(self.freqs[nimag:] > 0), "Imaginary frequencies found!"
        self.thermo = HarmonicThermo(
                self.freqs[nimag:],
                electronicenergy=self.e_elec,
                )
        self.coord = coord
    def copy(self):
        return self.__class__(self.outcar, self.T, self.ts)

class Shomate(_Thermo):
    def __init__(self, label, elements, shomatepars, gas, T, coord=1):
        self.label = label
        self.elements = elements
        self.shomatepars = shomatepars
        self.A = shomatepars[0]
        self.B = shomatepars[1]
        self.C = shomatepars[2]
        self.D = shomatepars[3]
        self.E = shomatepars[4]
        self.F = shomatepars[5]
        self.G = shomatepars[6]
        self.T = T
        self.gas = gas
        self.coord = coord
    def get_enthalpy(self, T=None):
        if T is None:
            T = self.T
        t = T / 1000
        H = self.A*t + self.B*t**2/2. + self.C*t**3/3. + self.D*t**4/4. \
                - self.E/t + self.F
        H *= kJ / mol
        return H
    def get_entropy(self, T=None, P=None):
        if T is None:
            T = self.T
        t = T / 1000
        S = self.A*np.log(t) + self.B*t + self.C*t**2/2. + self.D*t**3/3. \
                - self.E/(2*t**2) + self.G
        S *= J / mol
        return S
    def copy(self):
        return self.__class__(self.label, self.shomatepars, self.gas, self.T)
    def __repr__(self):
        return self.label

class _Reactants(object):
    def __init__(self, species):
        self.species = []
        self.elements = {}
        for other in species:
            if isinstance(other, _Reactants):
                # If we're adding a _Reactants object to another
                # _Reactants object, just merge species and elements.
                self.species += other.species
                for key in other.elements:
                    if key in self.elements:
                        self.elements[key] += other.elements[key]
                    else:
                        self.elements[key] = other.elements[key]
            elif isinstance(other, _Thermo):
                # If we're adding a _Thermo object to a reactants
                # object, append the _Thermo to species and update
                # elements
                self.species.append(other)
                if isinstance(other, Shomate):
                    for symbol in other.elements:
                        if symbol in self.elements:
                            self.elements[symbol] += other.elements[symbol]
                        else:
                            self.elements[symbol] = other.elements[symbol]
                else:
                    for symbol in other.atoms.get_chemical_symbols():
                        if symbol in self.elements:
                            self.elements[symbol] += 1
                        else:
                            self.elements[symbol] = 1
            else:
                raise NotImplementedError
    def get_enthalpy(self, T=None):
        return sum([species.get_enthalpy(T) for species in self.species])
    def get_entropy(self, T=None, P=None):
        return sum([species.get_entropy(T, P) for species in self.species])
    def get_energy(self):
        return sum([species.get_energy() for species in self.species])
    def copy(self):
        return self.__class__(self.species)
    def get_mass(self):
        return sum([species.atoms.get_masses().sum() for species in self.species])
    def __iadd__(self, other):
        if isinstance(other, _Reactants):
            self.species += other.species
            for key in other.elements:
                if key in self.elements:
                    self.elements[key] += other.elements[key]
                else:
                    self.elements[key] = other.elements[key]
        elif isinstance(other, _Thermo):
            self.species.append(other)
            for symbol in other.atoms.get_chemical_symbols():
                if symbol in self.elements:
                    self.elements[symbol] += 1
                else:
                    self.elements[symbol] = 1
        else:
            raise NotImplementedError
    def __add__(self, other):
        new = self.copy()
        new += other
        return new
    def __imul__(self, factor):
        raise ValueError, "Reactants can only be multiplied by an integer"
        self.species *= factor
        for key in self.elements:
            self.elements[key] *= factor
    def __mul__(self, factor):
        new = self.copy()
        new *= factor
        return new
    def __rmul__(self, factor):
        return self.__mul__(factor)
    def __repr__(self):
        return ' + '.join([species.__repr__() for species in self.species])
    def __getitem__(self, i):
        return self.species[i]
    def __getslice__(self, i, j):
        return _Reactants([self.species[i:j]])
    def __len__(self):
        return len(self.species)