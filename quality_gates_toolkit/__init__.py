"""quality-gates-toolkit: importable Python implementation of the toolkit.

The judge engine (``quality_gates_toolkit.review``) and its support modules
live here so cross-repo consumers can pip-install this distribution and
import the judge API without the top-level ``scripts`` package-name
collision (see DECISIONS.md D-0017). ``scripts`` remains the console-script
package (``secret-scan``) and hosts backward-compatibility shims for the
moved modules.
"""
