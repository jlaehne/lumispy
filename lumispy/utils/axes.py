# -*- coding: utf-8 -*-
# Copyright 2019 The LumiSpy developers
#
# This file is part of LumiSpy.
#
# LumiSpy is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# LumiSpy is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with LumiSpy.  If not, see <http://www.gnu.org/licenses/>.

import numpy as np
import scipy.constants as c

from hyperspy.axes import DataAxis

from inspect import getfullargspec
from scipy import interpolate


#
# Functions needed for signal axis conversion
#

def _n_air(wl):
    """Refractive index of air as a function of WL in nm.

    This analytical function is correct for the range 185-1700 nm.

    According to `E.R. Peck and K. Reeder. Dispersion of air, 
    J. Opt. Soc. Am. 62, 958-962 (1972).`
    """
    wl = wl / 1000
    return 1 + 806051e-10 + 2480990e-8/(132274e-3 - 1/wl**2) + \
           174557e-9/(3932957e-5 - 1/wl**2)


def nm2eV(x):
    """Converts wavelength (nm) to energy (eV) using wavelength-dependent 
    permittivity of air.
    """
    return 1e9 * c.h * c.c / (c.e * _n_air(x) * x)


def eV2nm(x):
    """Converts energy (eV) to wavelength (nm) using wavelength-dependent 
    permittivity of air.
    """
    wl = 1239.5/x # approximate WL to obtain permittivity
    return 1e9 * c.h * c.c / (c.e * _n_air(wl) * x)


def axis2eV(ax0):
    """Converts given signal axis to eV using wavelength dependent permittivity 
    of air. Assumes WL in units of nm 
    unless the axis units are specifically set to µm.
    """
    if ax0.units == 'eV':
        raise AttributeError('Signal unit is already eV.')
    # transform axis, invert direction
    if ax0.units == 'µm':
        evaxis=nm2eV(1000*ax0.axis)[::-1]
        factor = 1e3 # correction factor for intensity
    else:
        evaxis=nm2eV(ax0.axis)[::-1]
        factor = 1e6
    axis = DataAxis(axis = evaxis, name = 'Energy', units = 'eV', 
                    navigate=False)

    return axis,factor


def data2eV(data, factor, ax0, evaxis):
    """The intensity is converted from counts/nm (counts/µm) to counts/meV by 
    doing a Jacobian transformation, see e.g. Wang and Townsend, J. Lumin. 142, 
    202 (2013). Ensures that integrates signals are still correct.
    """
    return data * factor * c.h * c.c / (c.e * _n_air(ax0[::-1])
           * evaxis**2)

#
# spectrum manipulation
#


def join_spectra(S,r=50,average=False,kind='slinear'):
    """ Takes list of Signal1D objects and returns a single object with all
    spectra joined. Joins spectra at the center of the overlapping range.
    Scales spectra by a factor determined as average over the range
    `center -/+ r` pixels. Axes
    
    Parameters
    ----------
    S : list of Signal1D objects (with overlapping signal axes)
    r : int, optional
        Number of pixels left/right of center (default `50`) defining the range
        over which to determine the scaling factor, has to be less than half
        of the overlapping pixels.
    average : boolean, optional
        If `True`, use average of data values within the range defined by `r`
        instead of joining at the center of the range (default).
    kind : str, optional
        Interpolation method (default 'slinear') to use when joining signals
        with a uniform signal axes. See `scipy.interpolate.interp1d` for
        options.
    
    Returns
    -------
    A new Signal1D object containing the joined spectra (properties are copied
    from first spectrum).
    """
    
    import numpy as np
    import os
    
    # Test that spectra overlap
    for i in range(1,len(S)):
        if S[i-1].axes_manager.signal_axes[0].axis.max() \
           < S[i].axes_manager.signal_axes[0].axis.min():
            raise ValueError("Signal axes not overlapping")
    
    # take first spectrum as basis
    S1 = S[0].deepcopy()
    axis = S1.axes_manager.signal_axes[0]
    for i in range(1,len(S)): # join following spectra
        S2 = S[i].deepcopy()
        axis2 = S2.axes_manager.signal_axes[0]
        omax = axis.axis.max() # define overlap range
        omin = axis2.axis.min()
        ocenter = (omax+omin)/2 # center of overlap range
        # closest index to center of overlap first spectrum
        ind1 = axis.value2index(ocenter)
        # closest index to center of overlap second spectrum
        ind2 = axis2.value2index(ocenter)
        # Make sure the corresponsing values are in correct order
        if axis.axis[ind1] > axis2.axis[ind2]:
            ind2 += 1
        # Test that r is not too large
        if (axis.value2index(omax) - ind1) <= r:
            raise ValueError("`r` is too large")
        # calculate mean deviation over defined range ignoring nan or zero values
        init = np.empty(S2.isig[ind2-r:ind2+r].data.shape)
        init[:] = np.nan
        factor = np.nanmean(np.divide(S1.isig[ind1-r:ind1+r].data,
                 S2.isig[ind2-r:ind2+r].data, out = init,
                 where = S2.isig[ind2-r:ind2+r].data != 0), axis = -1)
        S2.data = (S2.data.T * factor).T # scale 2nd spectrum by factor
        
        # for UniformDataAxis
        if not 'axis' in getfullargspec(DataAxis)[0] or axis.is_uniform:
            # join axis vectors  
            axis.size = axis.axis[:ind1].size + np.floor((axis2.axis[-1] - axis.axis[ind1])/axis.scale)
            # join data vectors interpolating to a common uniform axis
            if average: # average over range
                ind2r = axis2.value2index(axis.axis[ind1-r])
                f = interpolate.interp1d(axis2.axis[ind2r:],
                          S2.isig[ind2r:].data,kind='slinear')
                S1.data = np.hstack((S1.isig[:ind1-r+1].data,
                          np.mean([S1.isig[ind1-r+1:ind1+r].data,
                          f(axis.axis[ind1-r+1:ind1+r])],axis=0),
                          f(axis.axis[ind1+r:])))
            else: # just join at center of overlap
                f = interpolate.interp1d(axis2.axis[ind2:], 
                          S2.isig[ind2:].data,kind='slinear')
                S1.data = np.hstack((S1.isig[:ind1+1].data,
                          f(axis.axis[ind1+1:])))
        else: # for DataAxis/FunctionalDataAxis (non uniform)
            # convert FunctionalDataAxes to DataAxes
            if hasattr(axis,'expression'):
                axis.convert_to_non_uniform_axis()
            if hasattr(axis2,'expression'):
                axis2.convert_to_non_uniform_axis()
            # join axis vectors  
            axis.axis = np.hstack((axis.axis[:ind1],axis2.axis[ind2:]))
            if average: # average over range
                S1.data = np.hstack((S1.isig[:ind1-r].data,
                          np.mean([S1.isig[ind1-r:ind1+r].data,
                          S2.isig[ind2-r:ind2+r]],axis=0),
                          S2.isig[ind2+r:]))
            else: # just join at center of overlap
                S1.data = np.hstack((S1.isig[:ind1].data,
                          S2.isig[ind2:]))
    return S1
