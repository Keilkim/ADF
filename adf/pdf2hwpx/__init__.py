"""PDF -> editable HWPX (Hancom Office) conversion component.

Pipeline: pdf_reader measures a page, extract turns it into validated
blocks (schema), an optional ai backend may transcribe instead, reconcile
keeps measured styles, hwpx_writer writes the blocks and convert runs the
whole document. See README.md for the Hancom layout rules this relies on.
"""
from .ai import DEFAULT_MODEL, MODELS, GeminiClient, RelayClient
from .convert import convert_document, verify_package
from .extract import native_page
from .hwpx_writer import write_page
from .schema import ConversionError, validate_page

__all__ = ['DEFAULT_MODEL', 'MODELS', 'ConversionError', 'GeminiClient', 'RelayClient', 'convert_document',
           'native_page', 'validate_page', 'verify_package', 'write_page']
