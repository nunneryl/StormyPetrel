import {
  HEIGHT_BASIS_COLOR,
  HEIGHT_BASIS_LABEL,
  HEIGHT_BASIS_MARK,
  type HeightBasis,
} from '@/lib/heightBasis';

/**
 * The compact marker that follows a published height wherever there is no room for the full
 * label: the home page, the map popup (its HTML twin is heightBasisMarkHtml), search, the
 * region list and the cams page. A word, not a symbol, so it reads without a legend; the full
 * label is its tooltip. `basis` must come from heightBasis() on the SAME row whose height it
 * follows — heightBasis.test.mts fails on a hard-coded one.
 */
export function HeightBasisMark({ basis }: { basis: HeightBasis | null }) {
  if (basis === null) return null;
  return (
    <span
      title={HEIGHT_BASIS_LABEL[basis]}
      className="text-[10px] font-medium leading-none whitespace-nowrap"
      style={{ color: HEIGHT_BASIS_COLOR[basis] }}
    >
      {HEIGHT_BASIS_MARK[basis]}
    </span>
  );
}
