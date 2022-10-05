'''
This module provides the same interface as the older, non-tag version of datamodels
for the whole asdf file. It will start very basic, initialy only to support running
of the flat field step, but many other methods and capabilities will be added to
keep consistency with the JWST data model version.

It is to be subclassed by the various types of data model variants for products
'''
from pathlib import PurePath
import datetime
import sys
import asdf
import os
import os.path
import copy
import numpy as np
from astropy.time import Time
from asdf.fits_embed import AsdfInFits
from . import stnode
from . import validate
from . extensions import DATAMODEL_EXTENSIONS


__all__ = [
    'DataModel',
    'ImageModel',
    'ScienceRawModel',
    'RampModel',
    'RampFitOutputModel',
    'GuidewindowModel',
    'FlatRefModel',
    'DarkRefModel',
    'DistortionRefModel',
    'GainRefModel',
    'LinearityRefModel',
    'MaskRefModel',
    'PixelareaRefModel',
    'ReadnoiseRefModel',
    'SuperbiasRefModel',
    'SaturationRefModel',
    'WfiImgPhotomRefModel',
    'open',
]


class DataModel:
    '''Base class for all top level datamodels'''

    crds_observatory = 'roman'

    def __init__(self, init=None, **kwargs):
        self._iscopy = False
        self._shape = None
        self._instance = None
        self._asdf = None
        if init is None:
            asdffile = self.open_asdf(init=None, **kwargs)
        elif isinstance(init, (str, bytes, PurePath)):
            if isinstance(init, PurePath):
                init = str(init)
            if isinstance(init, bytes):
                init = init.decode(sys.getfilesystemencoding())
            asdffile = self.open_asdf(init, **kwargs)
            if not self.check_type(asdffile):
                raise ValueError(
                    f'ASDF file is not of the type expected. Expected {self.__class__.__name__}')
            self._instance = asdffile.tree['roman']
        elif isinstance(init, asdf.AsdfFile):
            asdffile = init
            self._asdf = asdffile
            self._instance = asdffile.tree['roman']
        elif isinstance(init, stnode.TaggedObjectNode):
            self._instance = init
            asdffile = asdf.AsdfFile()
            asdffile.tree = {'roman': init}
        else:
            raise IOError("Argument does not appear to be an ASDF file"
                          " or TaggedObjectNode.")
        self._asdf = asdffile

    def check_type(self, asdffile_instance):
        '''
        Subclass is expected to check for proper type of node
        '''
        if 'roman' not in asdffile_instance.tree:
            raise ValueError(
                'ASDF file does not have expected "roman" attribute')
        topnode = asdffile_instance.tree['roman']
        if model_registry[topnode.__class__] != self.__class__:
            return False
        return True

    @property
    def schema_uri(self):
        # Determine the schema corresonding to this model's tag
        schema_uri = next(t for t in DATAMODEL_EXTENSIONS[0].tags
                          if t.tag_uri == self._instance._tag).schema_uri
        return schema_uri

    def close(self):
        if not self._iscopy:
            if self._asdf is not None:
                self._asdf.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __del__(self):
        """Ensure closure of resources when deleted."""
        self.close()

    def copy(self, memo=None):
        result = self.__class__(init=None)
        self.clone(result, self, deepcopy=True, memo=memo)
        return result

    __copy__ = __deepcopy__ = copy

    @staticmethod
    def clone(target, source, deepcopy=False, memo=None):
        if deepcopy:
            instance = copy.deepcopy(source._instance, memo=memo)
            target._asdf = source._asdf.copy()
            target._instance = instance
            target._iscopy = True
        else:
            target._asdf = source._asdf
            target._instance = source._instance
            target._iscopy = True

        target._files_to_close = []
        target._shape = source._shape
        target._ctx = target

    def save(self, path, dir_path=None, *args, **kwargs):
        if callable(path):
            path_head, path_tail = os.path.split(path(self.meta.filename))
        else:
            path_head, path_tail = os.path.split(path)
        base, ext = os.path.splitext(path_tail)
        if isinstance(ext, bytes):
            ext = ext.decode(sys.getfilesystemencoding())

        if dir_path:
            path_head = dir_path
        output_path = os.path.join(path_head, path_tail)

        # TODO: Support gzip-compressed fits
        if ext == '.asdf':
            self.to_asdf(output_path, *args, **kwargs)
        else:
            raise ValueError("unknown filetype {0}".format(ext))

        return output_path

    def open_asdf(self, init=None, **kwargs):
        if isinstance(init, str):
            asdffile = asdf.open(init)
        else:
            asdffile = asdf.AsdfFile(init)
        return asdffile

    def to_asdf(self, init, *args, **kwargs):
        # self.on_save(init)

        asdffile = self.open_asdf(**kwargs)
        asdffile.tree = {'roman': self._instance}
        asdffile.write_to(init, *args, **kwargs)

    def get_primary_array_name(self):
        """
        Returns the name "primary" array for this model, which
        controls the size of other arrays that are implicitly created.
        This is intended to be overridden in the subclasses if the
        primary array's name is not "data".
        """
        if hasattr(self, 'data'):
            primary_array_name = 'data'
        else:
            primary_array_name = ''
        return primary_array_name

    @property
    def override_handle(self):
        """override_handle identifies in-memory models where a filepath
        would normally be used.
        """
        # Arbitrary choice to look something like crds://
        return "override://" + self.__class__.__name__

    @property
    def shape(self):
        if self._shape is None:
            primary_array_name = self.get_primary_array_name()
            if primary_array_name and hasattr(self, primary_array_name):
                primary_array = getattr(self, primary_array_name)
                self._shape = primary_array.shape
        return self._shape

    def __setattr__(self, attr, value):
        if attr.startswith('_'):
            self.__dict__[attr] = value
        else:
            setattr(self._instance, attr, value)

    def __getattr__(self, attr):
        return getattr(self._instance, attr)

    def __setitem__(self, key, value):
        if key.startswith('_'):
            raise ValueError(
                'May not specify attributes/keys that start with _')
        if hasattr(self._instance, key):
            setattr(self._instance, key, value)
        else:
            self._instance._data[key] = value

    def to_flat_dict(self, include_arrays=True):
        """
        Returns a dictionary of all of the model items as a flat dictionary.

        Each dictionary key is a dot-separated name.  For example, the
        model element ``meta.observation.date`` will end up in the
        dictionary as::

            { "meta.observation.date": "2012-04-22T03:22:05.432" }

        This differs from the JWST data model in that the schema is not
        directly used
        """
        def convert_val(val):
            if isinstance(val, datetime.datetime):
                return val.isoformat()
            elif isinstance(val, Time):
                return str(val)
            return val

        if include_arrays:
            return dict(('roman.' + key, convert_val(val)) for (key, val) in self.items())
        else:
            return dict(('roman.' + key, convert_val(val)) for (key, val) in self.items()
                        if not isinstance(val, np.ndarray))

    def items(self):
        """
        Iterates over all of the model items in a flat way.

        Each element is a pair (``key``, ``value``).  Each ``key`` is a
        dot-separated name.  For example, the schema element
        ``meta.observation.date`` will end up in the result as::

            ("meta.observation.date": "2012-04-22T03:22:05.432")

        Unlike the JWST DataModel implementation, this does not use
        schemas directly.
        """
        def recurse(tree, path=[]):
            if isinstance(tree, (stnode.DNode, dict)):
                for key, val in tree.items():
                    for x in recurse(val, path + [key]):
                        yield x
            elif isinstance(tree, (stnode.LNode, list, tuple)):
                for i, val in enumerate(tree):
                    for x in recurse(val, path + [i]):
                        yield x
            elif tree is not None:
                yield ('.'.join(str(x) for x in path), tree)

        for x in recurse(self._instance):
            yield x

    def get_crds_parameters(self):
        """
        Get parameters used by CRDS to select references for this model.

        Returns
        -------
        dict
        """
        crds_header = {
            key: val for key, val in self.to_flat_dict(include_arrays=False).items()
            if isinstance(val, (str, int, float, complex, bool))
        }
        return crds_header

    def validate(self):
        """
        Re-validate the model instance against the tags
        """
        validate.value_change(self._instance, pass_invalid_values=False,
                              strict_validation=True)

    def info(self, *args, **kwargs):
        return self._asdf.info(*args, **kwargs)

    def search(self, *args, **kwargs):
        return self._asdf.search(*args, **kwargs)

    def schema_info(self, *args, **kwargs):
        return self._asdf.schema_info(*args, **kwargs)

class ImageModel(DataModel):
    pass


class ScienceRawModel(DataModel):
    pass


class RampModel(DataModel):
    pass


class RampFitOutputModel(DataModel):
    pass


class GuidewindowModel(DataModel):
    pass


class FlatRefModel(DataModel):
    pass


class DarkRefModel(DataModel):
    pass


class DistortionRefModel(DataModel):
    pass


class GainRefModel(DataModel):
    pass


class LinearityRefModel(DataModel):
    def get_primary_array_name(self):
        """
        Returns the name "primary" array for this model, which
        controls the size of other arrays that are implicitly created.
        This is intended to be overridden in the subclasses if the
        primary array's name is not "data".
        """
        return 'coeffs'


class MaskRefModel(DataModel):
    def get_primary_array_name(self):
        """
        Returns the name "primary" array for this model, which
        controls the size of other arrays that are implicitly created.
        This is intended to be overridden in the subclasses if the
        primary array's name is not "data".
        """
        return 'dq'


class PixelareaRefModel(DataModel):
    pass


class ReadnoiseRefModel(DataModel):
    pass


class SuperbiasRefModel(DataModel):
    pass


class SaturationRefModel(DataModel):
    pass


class WfiImgPhotomRefModel(DataModel):
    pass


def open(init, memmap=False, target=None, **kwargs):
    """
    Data model factory function

    Parameters
    ----------
    init : str, `DataModel`, `asdf.AsdfFile`
        May be any one of the following types:
            - `asdf.AsdfFile` instance
            - string indicating the path to an ASDF file
            - `DataModel` Roman data model instance
    memmap : bool
        Open ASDF file binary data using memmap (default: False)
    target : `DataModel`
        If not None value, the `DataModel` implied by the init argument
        must be an instance of the target class. If the init value
        is already a data model, and matches the target, the init
        value is returned, not copied, as opposed to the case where
        the init value is a data model, and target is not supplied,
        and the returned value is a copy of the init value.

    Returns
    -------
    `DataModel`
    """
    if target is not None:
        if not issubclass(target, DataModel):
            raise ValueError("Target must be a subclass of DataModel")
    # Temp fix to catch JWST args defore being passed to asdf open
    if "asn_n_members" in kwargs:
        del kwargs["asn_n_members"]
    if isinstance(init, asdf.AsdfFile):
        asdffile = init
    elif isinstance(init, DataModel):
        if target is not None:
            if not isinstance(init, target):
                raise ValueError("First argument is not an instance of target")
            else:
                return init
        # Copy the object so it knows not to close here
        return init.copy()
    else:
        try:
            kwargs['copy_arrays'] = not memmap
            asdffile = asdf.open(init, **kwargs)
        except ValueError:
            raise TypeError(
                "Open requires a filepath, file-like object, or Roman datamodel")
        if isinstance(asdffile, AsdfInFits):
            raise TypeError(
                "Roman datamodels does not accept FITS files or objects")
    modeltype = type(asdffile.tree['roman'])
    if modeltype in model_registry:
        rmodel = model_registry[modeltype](asdffile, **kwargs)
        if target is not None:
            if not issubclass(rmodel.__class__, target):
                raise ValueError(
                    "Referenced ASDF file model type is not subclass of target")
        else:
            return rmodel
    else:
        return DataModel(asdffile, **kwargs)


model_registry = {
    stnode.WfiImage: ImageModel,
    stnode.WfiScienceRaw: ScienceRawModel,
    stnode.Ramp: RampModel,
    stnode.RampFitOutput: RampFitOutputModel,
    stnode.Guidewindow: GuidewindowModel,
    stnode.FlatRef: FlatRefModel,
    stnode.DarkRef: DarkRefModel,
    stnode.DistortionRef: DistortionRefModel,
    stnode.GainRef: GainRefModel,
    stnode.LinearityRef: LinearityRefModel,
    stnode.MaskRef: MaskRefModel,
    stnode.PixelareaRef: PixelareaRefModel,
    stnode.ReadnoiseRef: ReadnoiseRefModel,
    stnode.SaturationRef: SaturationRefModel,
    stnode.SuperbiasRef: SuperbiasRefModel,
    stnode.WfiImgPhotomRef: WfiImgPhotomRefModel,
}
