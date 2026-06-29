"""AristotleDataset — lightweight dataset path resolver."""

import niceview.utils.io as vio


class AristotleDataset:
    """Aristotle dataset."""

    def __init__(self, data_dir, data_extension, cache_dir, cache_extension, primary_key_list):
        """Initialize Aristotle dataset.

        Args:
            data_dir (str): data directory.
            data_extension (dict): data extension.
            cache_dir (str): cache directory.
            cache_extension (dict): cache extension.
            primary_key_list (list of str): list of primary keys.
        """
        self.data_dir = data_dir
        self.data_extension = data_extension
        self.cache_dir = cache_dir
        self.cache_extension = cache_extension
        self.primary_key_list = primary_key_list

    def get_data_field(self, primary_key, data_field):
        """Get data field.

        Args:
            primary_key (str): primary key.
            data_field (str): data field.

        Returns:
            str: data field path.

        Raises:
            ValueError: bad input primary key.
        """
        # if primary_key not in self.primary_key_list:
        #     raise ValueError('Bad input primary key')

        filename = self._unparse_filename(primary_key, data_field, self.data_extension[data_field])
        filepath = vio.join_path(self.data_dir, filename)
        return filepath

    def get_cache_field(self, primary_key, cache_field):
        """Get cache field.

        Args:
            primary_key (str): primary key.
            cache_field (str): cache field.

        Returns:
            str: cache field path.

        Raises:
            ValueError: bad input primary key.
        """
        # if primary_key not in self.primary_key_list:
        #     raise ValueError('Bad input primary key')

        filename = self._unparse_filename(
            primary_key, cache_field, self.cache_extension[cache_field],
        )
        filepath = vio.join_path(self.cache_dir, filename)
        return filepath

    def _unparse_filename(self, primary_key, field_name, extension):
        """Unparse filename.

        Args:
            primary_key (str): primary key.
            field_name (str): field name.
            extension (str): extension.

        Returns:
            str: filename.
        """
        filename = '-'.join([primary_key, field_name])
        filename = '.'.join([filename, extension])
        return filename
