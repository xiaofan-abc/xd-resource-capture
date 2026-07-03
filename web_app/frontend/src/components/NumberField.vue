<script setup lang="ts">
defineProps<{
  label: string;
  modelValue: number;
  min?: number;
  max?: number;
}>();

const emit = defineEmits<{
  "update:modelValue": [value: number];
}>();
</script>

<template>
  <label class="field">
    <span>{{ label }}</span>
    <input
      name="numeric-field"
      type="number"
      inputmode="numeric"
      :min="min || 1"
      :max="max"
      :value="modelValue"
      @input="emit('update:modelValue', Number(($event.target as HTMLInputElement).value))"
    />
  </label>
</template>
