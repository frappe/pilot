<template>
	<FrappeUIProvider>
		<RouterView />
	</FrappeUIProvider>
</template>

<script setup>
import { useTheme, FrappeUIProvider } from "frappe-ui";

const { initializeTheme } = useTheme();

initializeTheme();
</script>
