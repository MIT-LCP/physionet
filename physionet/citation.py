"""Citation data and parsing for published PhysioNet projects."""

from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Optional, Tuple


@dataclass(frozen=True)
class Citation:
    """A citation in plain text and, when available, BibTeX."""

    text: str
    bibtex: Optional[str]
    doi: Optional[str]
    url: str


@dataclass(frozen=True)
class CitationBundle:
    """A versioned project citation and the recommended PhysioNet citations."""

    project: Citation
    physionet: Tuple[Citation, ...]


# PhysioNet asks users to cite both papers at https://physionet.org/about/.
# Keep these references in sync with that page when its guidance changes.
STANDARD_PHYSIONET_CITATIONS = (
    Citation(
        text=(
            "Pollard T, Moody BE, Lehman L, Gow B, Fernandes C, Xie C, Johnson A, Mark RG, Heldt T. "
            "PhysioNet as a Global Platform for Biomedical Research. Nature Health. 2026;1:792-795. "
            "doi:10.1038/s44360-026-00096-z."
        ),
        bibtex=(
            "@article{pollard_physionet_2026,\n"
            "  author = {Pollard, Tom and Moody, Benjamin and Lehman, Li-Wei and Gow, Brian and "
            "Fernandes, Chrystinne and Xie, Chen and Johnson, Alistair and Mark, Roger and Heldt, Thomas},\n"
            "  title = {{PhysioNet} as a Global Platform for Biomedical Research},\n"
            "  journal = {Nature Health},\n"
            "  year = {2026},\n"
            "  volume = {1},\n"
            "  pages = {792--795},\n"
            "  doi = {10.1038/s44360-026-00096-z},\n"
            "  url = {https://doi.org/10.1038/s44360-026-00096-z}\n"
            "}"
        ),
        doi="10.1038/s44360-026-00096-z",
        url="https://doi.org/10.1038/s44360-026-00096-z",
    ),
    Citation(
        text=(
            "Goldberger AL, Amaral LAN, Glass L, Hausdorff JM, Ivanov PCh, Mark RG, Mietus JE, "
            "Moody GB, Peng C-K, Stanley HE. PhysioBank, PhysioToolkit, and PhysioNet: "
            "Components of a New Research Resource for Complex Physiologic Signals. "
            "Circulation. 2000;101(23):e215-e220. doi:10.1161/01.CIR.101.23.e215."
        ),
        bibtex=(
            "@article{goldberger_physiobank_2000,\n"
            "  author = {Goldberger, Ary L. and Amaral, Luis A. N. and Glass, Leon and "
            "Hausdorff, Jeffrey M. and Ivanov, Plamen Ch. and Mark, Roger G. and "
            "Mietus, Joseph E. and Moody, George B. and Peng, Chung-Kang and Stanley, H. Eugene},\n"
            "  title = {{PhysioBank}, {PhysioToolkit}, and {PhysioNet}: Components of a New "
            "Research Resource for Complex Physiologic Signals},\n"
            "  journal = {Circulation},\n"
            "  year = {2000},\n"
            "  volume = {101},\n"
            "  number = {23},\n"
            "  pages = {e215--e220},\n"
            "  doi = {10.1161/01.CIR.101.23.e215},\n"
            "  url = {https://doi.org/10.1161/01.CIR.101.23.e215}\n"
            "}"
        ),
        doi="10.1161/01.CIR.101.23.e215",
        url="https://doi.org/10.1161/01.CIR.101.23.e215",
    ),
)


class ProjectBibTeXParser(HTMLParser):
    """Read the BibTeX that PhysioNet publishes on a project page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._in_bibtex = False
        self._parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "textarea" and dict(attrs).get("id") == "bibtex-data":
            self._in_bibtex = True

    def handle_endtag(self, tag):
        if tag == "textarea":
            self._in_bibtex = False

    def handle_data(self, data):
        if self._in_bibtex:
            self._parts.append(data)

    @property
    def bibtex(self) -> Optional[str]:
        return "".join(self._parts).strip() or None
