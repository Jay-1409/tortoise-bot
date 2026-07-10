# Two Sum

Given an array of integers `nums` and an integer `target`, return the indices of the two numbers such that they add up to `target`.

You may assume that each input has exactly one valid answer, and you may not use the same element twice.

Return the two indices in increasing order.

## Function Signature

Implement the following function:

```python
def two_sum(nums: list[int], target: int) -> list[int]:
```

For other languages, use the equivalent function name and behavior from the starter file.

## Input Format

The first line contains an integer `n`, the number of elements in the array.

The second line contains `n` space-separated integers.

The third line contains the integer `target`.

## Output Format

Print two space-separated integers: the indices of the two numbers that add up to `target`.

The indices must be printed in increasing order.

## Constraints

- `2 <= n <= 10^4`
- `-10^9 <= nums[i] <= 10^9`
- `-10^9 <= target <= 10^9`
- Exactly one valid answer exists.
- You cannot use the same element twice.

## Sample Input

```txt
4
2 7 11 15
9
```

## Sample Output

```txt
0 1
```

## Explanation

`nums[0] + nums[1] = 2 + 7 = 9`, so the answer is `0 1`.
