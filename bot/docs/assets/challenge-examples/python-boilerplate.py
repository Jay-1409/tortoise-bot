{{SOLUTION}}

n = int(input())
nums = list(map(int, input().split()))
target = int(input())

answer = two_sum(nums, target)
answer = sorted(answer)
print(answer[0], answer[1])
