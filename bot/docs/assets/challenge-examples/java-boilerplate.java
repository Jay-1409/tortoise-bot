import java.util.*;

public class Main {
    {{SOLUTION}}

    public static void main(String[] args) {
        Scanner scanner = new Scanner(System.in);

        int n = scanner.nextInt();
        int[] nums = new int[n];
        for (int i = 0; i < n; i++) {
            nums[i] = scanner.nextInt();
        }

        int target = scanner.nextInt();
        int[] answer = twoSum(nums, target);
        Arrays.sort(answer);

        System.out.println(answer[0] + " " + answer[1]);
    }
}
