// Order statistics without sorting: the k-th smallest of an array in
// expected O(n). Several ranks can be pulled from the same array; each call
// partially reorders it, which later calls tolerate.

/** k-th smallest value (0-based) of `a`, reordering `a` in place. */
export function selectInPlace(a: Float32Array | Float64Array, k: number): number {
  let lo = 0;
  let hi = a.length - 1;
  while (hi > lo) {
    // Median of three as the pivot keeps sorted and reversed input linear.
    const mid = (lo + hi) >>> 1;
    const x = a[lo]!;
    const y = a[mid]!;
    const z = a[hi]!;
    const pivot = x < y ? (y < z ? y : x < z ? z : x) : x < z ? x : y < z ? z : y;
    let i = lo;
    let j = hi;
    while (i <= j) {
      while (a[i]! < pivot) i++;
      while (a[j]! > pivot) j--;
      if (i <= j) {
        const t = a[i]!;
        a[i] = a[j]!;
        a[j] = t;
        i++;
        j--;
      }
    }
    if (k <= j) hi = j;
    else if (k >= i) lo = i;
    else return a[k]!;
  }
  return a[k]!;
}
